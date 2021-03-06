#!/usr/bin/env python

import cv
import platform
import numpy as np
import time
import argparse
import pickle
import copy
import logging
import zlib
import base64

from spacebrewLink import SpacebrewLink
from timeProfiler import TimeProfiler

client = False


WIDTH = 240
HEIGHT = 135

arch = platform.architecture()
if arch[0] == '32bit' and arch[1] == 'ELF' :
	# assume rpi, drop the wide format support
	WIDTH = 160
	HEIGHT = 120

# since we only do a non-zero test, this is arbitrary
MAX_PIXEL_VAL = 255

state = None
rectangle = None
tempRectangle = None
frameData = None
uncompressed = None

parser = argparse.ArgumentParser(description='Python based Region of Interest ROI sensor, RPI tested!')
parser.add_argument('name', 
					type=str,
				    help='The name of this client/sensor that will show in spacebrew',
				    metavar='sensor01')

parser.add_argument('-c', '--client', 
					help="Configure script to operate as a client to adjust/monitor sensors rather than act as a sensor.",
					action='store_true')

parser.add_argument('-v', '--visual',
					help="Show sensor windows, affects sensor mode only",
					action='store_true')

parser.add_argument('-p', '--pixels',
					help="Specify the width and height to initialize with",
					nargs=2,
					type=int,
					metavar=("width", "height"))

parser.add_argument('-s', '--server',
					help="Specify the spacebrew server if other than local",
					type=str,
					metavar='localhost')

parser.add_argument('-x', '--xoffset',
					help="Start all windows to the right by X pixels",
					type=int,
					metavar=600)

parser.add_argument('-z', '--zipLevel',
					help="Specify zlip compression level for frames. 0=none, 9=max.  Default 6",
					type=int,
					default=6,
					choices=range(0,10))

parser.add_argument('--profile',
					help="Start app using cProfiler",
					action='store_true')

parser.add_argument('-l', '--logging',
					type=str,
					help="Set the logging level",
					choices=['DEBUG', 'WARN', 'INFO'])

parser.add_argument('-i', '--interval',
					help='Seconds between sending frames',
					type=int,
					default=5)


args = parser.parse_args()

if args.pixels:
	WIDTH = args.pixels[0]
	HEIGHT = args.pixels[1]

if args.logging:
	logging.getLogger().setLevel(args.logging)
	logging.info("Set logging level to {0}".format(args.logging))
else:
	logging.getLogger().setLevel("INFO")

if args.server:
	sbLink = SpacebrewLink(args.name, args.server)
else:
	sbLink = SpacebrewLink(args.name)


tp = TimeProfiler(logging.debug)

if not args.profile:
	tp.disable()


def mouseCallback(event, x, y, flags, param):
	global state
	global rectangle
	global tempRectangle
	global sbLink

	if event == cv.CV_EVENT_LBUTTONDOWN:
		state = "DOWN"
		tempRectangle = [x, y, x, y]
		rectangle = None
		
	elif event == cv.CV_EVENT_LBUTTONUP:
		if state != "DOWN" :
			pass

		state = None

		size = abs(tempRectangle[0] - tempRectangle[2]) * abs(tempRectangle[1] - tempRectangle[3])
		if size > 0:
			rectangle = tempRectangle
			
			# client broadcasts the ROI
			if args.client:
				sbLink.setFrameROI(pickle.dumps(rectangle))
				# set it to None, wait for it to come back
				rectangle = None
	
		tempRectangle = None

	elif event == cv.CV_EVENT_MOUSEMOVE:
		if state == "DOWN" :
			tempRectangle[2] = x
			tempRectangle[3] = y
			#print "win ", x, y


if args.client:
	capture = None
else:
	capture = cv.CaptureFromCAM(-1)
	cv.SetCaptureProperty( capture, cv.CV_CAP_PROP_FRAME_WIDTH, WIDTH )
	cv.SetCaptureProperty( capture, cv.CV_CAP_PROP_FRAME_HEIGHT, HEIGHT )

# sometimes we don't get back the same image size we request (camera dependent)
# so let's get one frame and adjust as needed

frame = None
while frame is None:
	frame = cv.QueryFrame(capture)
	c = cv.WaitKey(100)

if WIDTH != frame.width or HEIGHT != frame.height:
	WIDTH = frame.width
	HEIGHT = frame.height

	string = "Camera returned frame size {0},{1}.  Adjusted accordingly.".format(WIDTH, HEIGHT)
	logging.info(string)


accumulator32f =    cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_32F, 1 )
grayBackground32f = cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_32F, 1 )
difference32f =     cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_32F, 1 )

frame =				cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_8U, 3 ) # RGB
accumulatorShow8u = cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_8U, 1 )
differenceShow8u =  cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_8U, 1 )
threshold8u = 	 	cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_8U, 1 )

lastSend = 0

def updateSettingsValues():
	cv.SetTrackbarPos("ThresholdCutoff", "Settings", sbLink.thresholdCutoff())
	cv.SetTrackbarPos("LearnRate", "Settings", sbLink.learnRate())
	cv.SetTrackbarPos("ConstantMessaging", "Settings", sbLink.constantMessaging())
	cv.SetTrackbarPos("PercentageFill", "Settings", sbLink.percentageFill())


def configureSettingsWindow():
	cv.ResizeWindow("Settings", WIDTH * 2, 75)

	cv.CreateTrackbar("ThresholdCutoff", 	"Settings", sbLink.thresholdCutoff(), 	255, 	sbLink.setThresholdCutoff )
	cv.CreateTrackbar("LearnRate", 		 	"Settings", sbLink.learnRate(), 		100, 	sbLink.setLearnRate )
	cv.CreateTrackbar("ConstantMessaging", 	"Settings", sbLink.constantMessaging(), 1, 		sbLink.setConstantMessaging)
	cv.CreateTrackbar("PercentageFill", 	"Settings", sbLink.percentageFill(), 	100, 	sbLink.setPercentageFill)


def moveClientWindows():

	o=0
	#print args
	if args.xoffset:
		o = args.xoffset		

	cv.MoveWindow("Camera", 		o+0, 			0)
	cv.MoveWindow("Accumulator", 	o+WIDTH + 10, 	0)
	cv.MoveWindow("Difference", 	o+0, 			HEIGHT + 50)
	cv.MoveWindow("Threshold", 		o+WIDTH + 10, 	HEIGHT + 50)
	cv.MoveWindow("Settings",       o+0,          	HEIGHT * 3 + 100)


def clientSetup():
	global sbLink

	cv.NamedWindow("Camera", cv.CV_WINDOW_AUTOSIZE)
	cv.NamedWindow("Accumulator", cv.CV_WINDOW_AUTOSIZE)
	cv.NamedWindow("Difference", cv.CV_WINDOW_AUTOSIZE)
	cv.NamedWindow("Threshold", cv.CV_WINDOW_AUTOSIZE)
	cv.NamedWindow("Settings", cv.CV_WINDOW_AUTOSIZE)

	sbLink.add("thresholdCutoff", 	"int", 		20, 	"dir_out")
	sbLink.add("learnRate", 		"int", 		10, 	"dir_out")
	sbLink.add("constantMessaging", "bool", 	False, 	"dir_out")
	sbLink.add("percentageFill", 	"int", 		20, 	"dir_out")
	sbLink.add("frameROI",			"string",	"",		"dir_out")
	sbLink.add("frames", 			"string", 	"", 	"dir_in")

	sbLink.start()

	moveClientWindows()
	configureSettingsWindow()

	cv.SetMouseCallback("Camera", mouseCallback, None)


def sensorSetup():
	global sbLink

	sbLink.add("thresholdCutoff", 	"int", 		20, 	"dir_in")
	sbLink.add("learnRate", 		"int", 		10, 	"dir_in")
	sbLink.add("constantMessaging", "bool", 	False, 	"dir_in")
	sbLink.add("percentageFill", 	"int", 		20, 	"dir_in")
	sbLink.add("frameROI",			"string",	"",		"dir_in")
	sbLink.add("frames", 			"string", 	"", 	"dir_out")
	sbLink.add("hitPercentage",		"string",	"",		"dir_out")

	sbLink.start()

	if args.visual:
		cv.NamedWindow("Camera", cv.CV_WINDOW_AUTOSIZE)
		cv.NamedWindow("Accumulator", cv.CV_WINDOW_AUTOSIZE)
		cv.NamedWindow("Difference", cv.CV_WINDOW_AUTOSIZE)
		cv.NamedWindow("Threshold", cv.CV_WINDOW_AUTOSIZE)
		cv.NamedWindow("Settings", cv.CV_WINDOW_AUTOSIZE)

		configureSettingsWindow()

		cv.MoveWindow("Camera", 		0, 			0)
		cv.MoveWindow("Accumulator", 	WIDTH + 10, 0)
		cv.MoveWindow("Difference", 	0, 			HEIGHT + 50)
		cv.MoveWindow("Threshold", 		WIDTH + 10, HEIGHT + 50)
		cv.MoveWindow("Settings",       0,          HEIGHT * 2 + 100)

		cv.SetMouseCallback("Camera", mouseCallback, None)


def handleRectangleDraw():

	global frame
	global accumulatorShow8u
	global differenceShow8u
	global threshold8u

	# draw square
	if tempRectangle != None:
		pt1 = (tempRectangle[0], tempRectangle[1])
		pt2 = (tempRectangle[2], tempRectangle[3])
		
		cv.Rectangle(frame, pt1, pt2, (255,255,255), 3)
		cv.Rectangle(frame, pt1, pt2, (0,0,0), 1)

		cv.Rectangle(accumulatorShow8u, pt1, pt2, (255,255,255), 3)
		cv.Rectangle(accumulatorShow8u, pt1, pt2, (0,0,0), 1)

		cv.Rectangle(differenceShow8u, pt1, pt2, (255,255,255), 3)
		cv.Rectangle(differenceShow8u, pt1, pt2, (0,0,0), 1)

		cv.Rectangle(threshold8u, pt1, pt2, (255,255,255), 3)
		cv.Rectangle(threshold8u, pt1, pt2, (0,0,0), 1)


	elif rectangle != None:
		pt1 = (rectangle[0], rectangle[1])
		pt2 = (rectangle[2], rectangle[3])

		cv.Rectangle(frame, pt1, pt2, (255,255,255), 3)
		cv.Rectangle(frame, pt1, pt2, (0,0,0), 1)

		cv.Rectangle(accumulatorShow8u, pt1, pt2, (255,255,255), 3)
		cv.Rectangle(accumulatorShow8u, pt1, pt2, (0,0,0), 1)

		cv.Rectangle(differenceShow8u, pt1, pt2, (255,255,255), 3)
		cv.Rectangle(differenceShow8u, pt1, pt2, (0,0,0), 1)

		cv.Rectangle(threshold8u, pt1, pt2, (255,255,255), 3)
		cv.Rectangle(threshold8u, pt1, pt2, (0,0,0), 1)


def clientRepeat():

	global WIDTH
	global HEIGHT

	global frame
	global accumulatorShow8u
	global differenceShow8u
	global threshold8u
	global frameData
	global uncompressed

	if sbLink.framesRefreshed():
		# mark frames as read
		sbLink.framesRead()

		frameData = pickle.loads(base64.b64decode(sbLink.frames()))

		
		if frameData["WIDTH"] != WIDTH or frameData["HEIGHT"] != HEIGHT:
			WIDTH = frameData["WIDTH"]
			HEIGHT = frameData["HEIGHT"]

			# move client windows
			moveClientWindows()

			frame =				cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_8U, 3 ) # RGB
			accumulatorShow8u = cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_8U, 1 )
			differenceShow8u =  cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_8U, 1 )
			threshold8u = 	 	cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_8U, 1 )



		sbLink.setThresholdCutoff(frameData["thresholdCutoff"])
		sbLink.setLearnRate(frameData["learnRate"])
 		sbLink.setConstantMessaging(frameData["constantMessaging"])
 		sbLink.setPercentageFill(frameData["percentageFill"])

 		updateSettingsValues()


 	if frameData is not None:

 		# TODO optimize this
 		frameData = pickle.loads(base64.b64decode(sbLink.frames()))

 		if frameData["compressed"]:
			cv.SetData(frame, zlib.decompress(frameData["frame"]))
			cv.SetData(accumulatorShow8u, zlib.decompress(frameData["accumulator"]))
			cv.SetData(differenceShow8u, zlib.decompress(frameData["difference"]))
			cv.SetData(threshold8u, zlib.decompress(frameData["threshold"]))
		else:
			cv.SetData(frame, copy.deepcopy(frameData["frame"]))
			cv.SetData(accumulatorShow8u, frameData["accumulator"])
			cv.SetData(differenceShow8u, frameData["difference"])
			cv.SetData(threshold8u, frameData["threshold"])

		handleRectangleDraw()

		cv.ShowImage("Camera", frame)
		cv.ShowImage("Accumulator", accumulatorShow8u)
		cv.ShowImage("Difference", differenceShow8u)
		cv.ShowImage("Threshold", threshold8u)


	
def sensorRepeat():
	global accumulator
	global sbLink
	global lastSend
	global frame
	global rectangle

	tp.start("sensorRepeat")


	frame = cv.QueryFrame(capture)
	if frame is None:
		logging.error("QueryFrame returned a None object")
		return

	#print "Frame info: "
	#print frame.width, frame.height


	frame32f = None

	# handle depth conversion if necessary
	# just keep it all 32 bits
	if frame.depth == 32 :
		frame32f = frame
	else:
		
		frame32f = cv.CreateImage( (WIDTH, HEIGHT), cv.IPL_DEPTH_32F, 3 )
		# py ocv handles the bit conversion for us
		cv.ConvertScale(frame, frame32f)

	# convert to grayscale
	cv.CvtColor(frame32f, grayBackground32f, cv.CV_RGB2GRAY)
	# make the running average bg
	cv.RunningAvg(grayBackground32f, accumulator32f, float(sbLink.learnRate()) / 100)
	# do a absolute diff
	cv.AbsDiff(accumulator32f, grayBackground32f, difference32f)
	# finally threshold
	cv.Threshold(difference32f, threshold8u, sbLink.thresholdCutoff(), MAX_PIXEL_VAL, cv.CV_THRESH_BINARY)

	cv.ConvertScale(accumulator32f, accumulatorShow8u)
	cv.ConvertScale(difference32f, differenceShow8u)
	
	# If we have a rectangle let's do the test
	if rectangle != None:
		r = rectangle
		
		# create height x width to be compatable with openCV form
		mask = np.zeros((threshold8u.height, threshold8u.width), np.uint8)

		minX = min(r[0], r[2])
		maxX = max(r[0], r[2])

		minY = min(r[1], r[3])
		maxY = max(r[1], r[3])

		# start on Y
		mask[minY:maxY, minX:maxX] = threshold8u[minY:maxY, minX:maxX]

		pix = abs(r[0]-r[2]) * abs(r[1] - r[3])		
		hits = np.count_nonzero(mask)
		pct = float(hits) / float(pix)

		if bool(sbLink.constantMessaging()) or pct > float(sbLink.percentageFill()) / 100:
			sbLink.setHitPercentage(pct)
			logging.debug("Hit percentage is {0}, \t\treporting it.".format(pct))
		else:
			logging.debug("Hit percentage is {0}, \t\tNOT reporting it.".format(pct))

		

	if sbLink.frameROIRefreshed():
		sbLink.frameROIRead()
		rectangle = pickle.loads(sbLink.frameROI())
		# reset lastSend so that the triangle is sent over immediately
		lastSend = 0


	handleRectangleDraw()

	if args.visual:
		cv.ShowImage("Camera", frame)
		cv.ShowImage("Accumulator", accumulatorShow8u)
		cv.ShowImage("Difference", differenceShow8u)
		cv.ShowImage("Threshold", threshold8u)

		# update track bars, in case spacebrew made an update
		updateSettingsValues()

	
	if lastSend + args.interval < time.mktime(time.gmtime()):
		# update images

		tp.start("PrepareFrames")

		frameData = {}
		frameData["WIDTH"] = WIDTH
		frameData["HEIGHT"] = HEIGHT
		frameData["thresholdCutoff"] = sbLink.thresholdCutoff()
		frameData["learnRate"] = sbLink.learnRate()
 		frameData["constantMessaging"] = sbLink.constantMessaging()		
 		frameData["percentageFill"] = sbLink.percentageFill()

		if args.zipLevel:

			tp.start("CompressFrames")

	 		frameData["compressed"] = True
	 		frameData["frame"] = zlib.compress(frame.tostring(), args.zipLevel)
 			frameData["accumulator"] = zlib.compress(accumulatorShow8u.tostring(), args.zipLevel)
 			frameData["difference"] = zlib.compress(differenceShow8u.tostring(), args.zipLevel)
 			frameData["threshold"] = zlib.compress(threshold8u.tostring(), args.zipLevel)
 			
 			tp.end("CompressFrames")

 		else:

	 		frameData["compressed"] = False
		 	frameData["frame"] = frame.tostring()
	 		frameData["accumulator"] = accumulatorShow8u.tostring()
	 		frameData["difference"] = differenceShow8u.tostring()
	 		frameData["threshold"] = threshold8u.tostring()

		tp.end("PrepareFrames")

		tp.start("PickleFrames")
 		pickledFrames = base64.b64encode(pickle.dumps(frameData))
		tp.end("PickleFrames")	 		

		tp.start("SendFrames")
		sbLink.setFrames(pickledFrames)
		tp.end("SendFrames")

		lastSend = time.mktime(time.gmtime())

	tp.end("sensorRepeat")



def main():
	global sbLink
	global capture
	global WIDTH
	global HEIGHT

	clean = False

	if args.client:
		clientSetup()
	else:
		sensorSetup()

	try:
		while True:
			c = cv.WaitKey(100)
			# 120 == 'x'
			if c == 120:
				break
			#time.sleep no work
			#time.sleep(int(sys.argv[1]))
			if args.client:
				clientRepeat()
			else:
				sensorRepeat()
		
		cv.DestroyAllWindows()
		if capture:
			del(capture)
		sbLink.stop()

		clean = True

	except (KeyboardInterrupt, SystemExit) as e:
		logging.info("Got keyboard interrupt")
		if not clean:
			cv.DestroyAllWindows()
			if capture: 
				del(capture)
			sbLink.stop()
	except Exception as e:
		if not clean:
			cv.DestroyAllWindows()
			if capture:
				del(capture)
			sbLink.stop()
		raise


if __name__ == '__main__':
	if args.profile:
		import cProfile
		cProfile.run('main()')
	else:
	    main()
