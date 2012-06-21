#!/home/ssi/jwhitmore/progs/epd-7.1-2-rh5-x86_64/bin/python
# 2011-11-16 helpreduce.py

import sys
import shutil
import os
import glob
import csv
import pprint 
import numpy as np
import scipy as sp
import subprocess as subprocess
import time
import datetime
from optparse import OptionParser
import argparse
from ConfigParser import RawConfigParser
import random as ra
import tempfile
import itertools
import fileinput
import shlex
import pylab as pl
import pyfits as pf
import cPickle as pickle
import scipy.interpolate as si
import scipy.signal as ss

import minuit as mi 
import scipy.constants as spc

c_light = spc.c

# TODO ceres.cleanup(): invalid value encountered in divide
# TODO wavelength cut-out regions
# TODO minimum bin-size requirement
# TODO think about integration interval (+/- 1/2 mindel)
# TODO make a flag that says whether each method is actually run.
# TODO implement logging so everything is reproducible.

help_message = '''
Various limitations: 
Must have an FTS spectrum w/o gaps
Must have a telescope spectrum w/ monotonically increasing wavelength (gaps are OK)
The spacing of the nearest two pixels in the telescope spectrum is used as the pixel size for each order.
'''

class Exposure(object):
  """docstring for Exposure"""
  def __init__(self, arcFile='', reductionProgram='', calibrationType='', calibrationFile='', exposureFile=''):
    """docstring for __init__"""
    super(Exposure, self).__init__()
    self.arcFile = arcFile # a calibration Arc File
    self.exposureFile = exposureFile # a calibration Arc File
    self.reductionProgram = reductionProgram # reduction software used
    self.calibrationType = calibrationType # Calibration type: iodine, asteroid, none
    self.calibrationFile = calibrationFile # Calibration File
    self.fitGuess = {}
    self.fitGuess['initial'] = { 'fshift':0.002, 'fix_fshift':False, 'limit_fshift':(-1.0,1.0) ,'err_fshift':0.005 }
    self.fitGuess['initial'].update({ 'fsigma':10.5, 'fix_fsigma':False, 'limit_fsigma':(2.0,2000) ,'err_fsigma':5 })
    self.fitGuess['initial'].update({ 'fmultiple':50.25, 'fix_fmultiple':False, 'limit_fmultiple':(0.1, 100.0) ,'err_fmultiple':0.2 })
    self.fitGuess['initial'].update({ 'fslope':0.0005, 'fix_fslope':False, 'limit_fslope':(-1.0,1.0) ,'err_fslope':0.05 })
    self.fitGuess['initial'].update({ 'elements':100, 'fix_elements':True })
    self.fitGuess['initial'].update({ 'fwidth':200, 'fix_fwidth':True })
    self.fitGuess['initial'].update({ 'strategy':2 })
    self.fitResults = {}
    self.tiltfitResults = {}
    self.BinResults = {}
    self.Bins = {}
    if self.exposureFile.split('.')[-1] == 'fits':
      print "A fits exposure file."
      self.Orders = {}
      hdu = pf.open(self.exposureFile)
      self.header = hdu[0].header
      for i,x in enumerate(hdu):
        try:
          type(hdu[i].data)
          self.Orders[i] = {}
          self.Orders[i]['wav'] = x.data[0]
          self.Orders[i]['flx'] = x.data[1]
          self.Orders[i]['err'] = x.data[2]
        except:
          self.exposureHeader = hdu[-1].header
    else:
      print "Not a fits file.", self.exposureFile
    pass

  def usage(self):
    """docstring for usage"""
    print "The general order goes: "
    print "loadReferenceSpectra, cleanup, continuumFit, chop, overSample, fullOrderShift, binShift"
    pass
  
  def loadReferenceSpectra(self):
    """docstring for loadReferenceSpectra"""
    try: 
      iow, iof = np.loadtxt(self.calibrationFile)
    except:
      print "Consider saving a faster-loading calibration file."
      iow, iof = np.loadtxt(self.calibrationFile, unpack='True')
    print iow[0], iow[-1]
    for x in self.Orders:
      if self.Orders[x]['wav'][0] > iow[0] + 50.0:
        try:
          ok = (self.Orders[x]['wav'][0] - 10 < iow) & (self.Orders[x]['wav'][-1] + 10 > iow)
          if len(iow[ok]) > 200:
            self.Orders[x]['iow'] = iow[ok]
            self.Orders[x]['iof'] = iof[ok]
          "Reference spectra worked"
        except:
          print "Outside overlap."
    pass
  
  def cleanup(self,verbose=False):
    """mask out bad regions of the spectra"""
    # Think about whether to overwrite the input files
    if verbose==True:
      print "Beginning cleanup of data...", datetime.datetime.now().strftime("%Y/%m/%d %H:%M:%S")
    errorcutoff = 0.0
    sncutoff = 10.0
    for x in self.Orders:
      ok = self.Orders[x]['err'] >= errorcutoff
      ok2 = self.Orders[x]['flx']/self.Orders[x]['err'] >= sncutoff
      for key in ['wav', 'flx', 'err']:
        self.Orders[x][key] = self.Orders[x][key][(ok & ok2)]
    # # TODO Deal with sky lines/known regions to exclude
    # dummywav = cleanwav
    # for x in zip(begin_kill_array, end_kill_array):
    #   dummywav = [y for y in dummywav if not (y > x[0] and y < x[1])]
    # finalindex = [np.argwhere(wav == x)[0][0] for x in dummywav]
    pass
  
  def continuumFit(self, knots=10, plot=False, verbose=False):
    """fits a continuum via a spline through the flux values."""
    knots = 10
    edgeTolerance = 0.1
    for x in self.Orders:
      s = si.LSQUnivariateSpline(self.Orders[x]['wav'],\
                                self.Orders[x]['flx'],\
                                np.linspace(self.Orders[x]['wav'][0]+edgeTolerance, self.Orders[x]['wav'][-1]-edgeTolerance, knots),\
                                w=self.Orders[x]['err'])
      self.Orders[x]['con'] = s(self.Orders[x]['wav']) # new array is made -- continuum
    pass
  
  def chop(self, edgebuffer=50):
    """program chops off the offending beginning and ending few pixels of each order"""
    for x in self.Orders:
      for key in ['wav', 'flx', 'err', 'con']:
        self.Orders[x][key] = self.Orders[x][key][edgebuffer:-edgebuffer]
    print "Chopped", edgebuffer, "pixels."
    pass
  
  def newOverSample(self):
    """sets the minimum spacing in the telescope spectra (mindel) for each order over the whole exposure.
    Rename. """
    for x in self.Orders:
      self.Orders[x]['mindel'] = self.Orders[x]['wav'][-1] - self.Orders[x]['wav'][0]
      for i in range(len(self.Orders[x]['wav']) - 1):
        if self.Orders[x]['mindel'] > self.Orders[x]['wav'][i+1] - self.Orders[x]['wav'][i]: 
          self.Orders[x]['mindel'] = self.Orders[x]['wav'][i+1] - self.Orders[x]['wav'][i]
    pass
  
  def newfullExposureShift(self, verbose=False, veryVerbose=False, robustSearch=False, binSize=350):
    """docstring for fullExposureShift"""
    starttime=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for x in self.Orders:
      if 'iow' in self.Orders[x]:
        print "Working on order: ", x
        self.newCreateBinArrays(order=x, binSize=binSize) # new!
        try:
          self.newOrderShiftandTilt(order=x, veryVerbose=veryVerbose) # new!
          self.newfullOrderBinShift(order=x, binSize=binSize)
        except:
          print "Order or bin failed."
    print "Finished working on exposure."
    print "Started: ", starttime, "Ended: ", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pass

  def newOrderShiftandTilt(self, order=7, verbose=False, veryVerbose=False, robustSearch=False):
    """docstring for dictionaryShift"""
    try:
      type(self.fitResults['order'])
    except:
      self.fitResults['order'] = {}
    try:
      type(self.fitResults['order'][order])
    except:
      self.fitResults['order'][order] = {}
    try:
      m = mi.Minuit(self.newshiftandtilt, order=order, fix_order=True, **self.fitGuess['initial'])
      if veryVerbose==True:
        m.printMode=1
      if robustSearch==True:
        print "Robust search. Beginning initial scan..."
        m.scan(("fshift",20,-0.5,0.5))
        print "done."
      # try: 
      print "Finding initial full order shift/fit", '\n', datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S") 
      m.migrad()
      self.fitResults['order'][order]['values'] = m.values
      try: 
        del self.fitResults['order'][order]['values']['order']
      except:
        pass
      self.fitResults['order'][order]['errors'] = m.errors
    except:
      print "Serious problem with order:", order
    pass

  # linear dispersion coefficient
  # spectral line depth
  # normalization: 
  # 2nd-order dispersion coefficient
  # width of main Gaussian IP
  # width of box IP
  # residuals
  # plot kernel
  # plot best fit between the two

  def gaussKernel(self, elements, sigma):
    """returns a normalized gaussian using scipy.signal"""
    return ss.gaussian(elements, sigma) / np.sum(ss.gaussian(elements, sigma))

  def newshiftandtilt(self, order, fmultiple, fshift, fsigma, elements, fslope, **kwargs):
    """trying to smooth, interpolate, and integrate the fit."""
    kernel = self.gaussKernel(elements, fsigma)
    s = si.UnivariateSpline(self.Orders[order]['iow'], np.convolve(kernel, (self.Orders[order]['iof'] * fmultiple) + fslope * (self.Orders[order]['iow'] - np.average(self.Orders[order]['iow'])), mode='same'), s=0)
    overflx = np.array([s.integral(x - self.Orders[order]['mindel']/2.0 + fshift, x + self.Orders[order]['mindel']/2.0 + fshift) for x in self.Orders[order]['wav']])
    return np.sum( ((overflx - self.Orders[order]['flx'] / self.Orders[order]['con']) / \
                    (self.Orders[order]['err'] / self.Orders[order]['con'])) ** 2 )
  
  def newCreateBinArrays(self, order=7, binSize=350, overlap=0.5):
    """overlap is the fractional overlap or how much the bin is shifted relative to the binSize. so overlapping by .5 shifts by half binSize; .33 by .33 binSize. """
    lamb = np.average(self.Orders[order]['wav'])
    try:
      type(self.fitResults[binSize])
    except:
      self.fitResults[binSize] = {}
    try:
      type(self.Orders[order][binSize])
      return
    except:
      self.Orders[order][binSize] = {}
    binAngstroms = lamb * binSize * 1000 / c_light
    temp = []
    for x in range(int(1.0/overlap)):
      temp.append(np.arange(self.Orders[order]['wav'][0] + overlap * x * binAngstroms, self.Orders[order]['wav'][-1] + overlap * x * binAngstroms, binAngstroms))

    np.append(temp[0], self.Orders[order]['wav'][-1]) # add last wavelength point to first bin edges array
    iowTolerance = 2.0
    self.Orders[order][binSize]['bins'] = {}
    COUNTER = 0
    for edgearray in temp:
      for i in range(len(edgearray) - 1):
        self.Orders[order][binSize]['bins'][COUNTER] = {}
        self.Orders[order][binSize]['bins'][COUNTER]['ok'] = (self.Orders[order]['wav'] > edgearray[i]) & (self.Orders[order]['wav'] <= edgearray[i + 1])
        self.Orders[order][binSize]['bins'][COUNTER]['iok'] = (self.Orders[order]['iow'] > edgearray[i] - iowTolerance) & (self.Orders[order]['iow'] <= edgearray[i + 1] + iowTolerance)
        COUNTER += 1
    pass
  
  def newfullOrderBinShift(self, order=7, binSize=350):
    """docstring for fullOrderBinShift"""
    # TODO check if createBinArrays has been run; if not; run first...
    try:
      type(self.fitResults[binSize])
    except:
      self.fitResults[binSize] = {}
    try:
      type(self.fitResults[binSize][order])
    except:
      self.fitResults[binSize][order] = {}
    try:
      type(self.fitResults[binSize][order]['bins'])
    except:
      self.fitResults[binSize][order]['bins'] = {}
    try:
      type(self.fitGuess['order'])
    except:
      self.fitGuess['order'] = {}
    try:
      type(self.fitGuess['order'][order])
    except:
      self.fitGuess['order'][order] = {}
    try:
      del self.fitGuess['initial']['order']
    except: 
      pass
    self.fitGuess['order'][order] = self.fitGuess['initial']
    self.fitGuess['order'][order].update(self.fitResults['order'][order]['values'])
    self.fitGuess['order'][order].update({ 'elements':int(10.0 * self.fitResults['order'][order]['values']['fsigma']) })
    for singlebin in self.Orders[order][binSize]['bins']:
      self.fitResults[binSize][order]['bins'][singlebin] = {}
      self.newsmallBinShift(order, binSize, singlebin)
    pass

  def newsmallBinShift(self, order=7, binSize=350, bin=2, veryVerbose=False, robustSearch=False):
    """docstring for smallBinShift"""
    # check that the full order solution has run.
    try:
      type(self.fitResults['order'][order]['values'])
    except:
      print "It doesn't look like the full order was run... "
    m = mi.Minuit(self.newbinshiftandtilt, order=order, binSize=binSize, bin=bin, fix_order=True, fix_binSize=True, fix_bin=True, **self.fitGuess['order'][order])
    if veryVerbose==True:
      m.printMode=1
    if robustSearch==True:
      print "Robust search. Beginning initial scan..."
      m.scan(("fshift",20,-0.5,0.5))
      print "done."
    try: 
      print datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "Finding initial shift/fit for order:", order, "and bin:", bin
      m.migrad()
      self.fitResults[binSize][order]['bins'][bin]['values'] = m.values
      self.fitResults[binSize][order]['bins'][bin]['errors'] = m.errors
      ok = self.Orders[order][binSize]['bins'][bin]['ok']
      iok = self.Orders[order][binSize]['bins'][bin]['iok']
      elements = self.fitResults[binSize][order]['bins'][bin]['values']['elements']
      lamb = np.average(self.Orders[order]['wav'][ok])
      cal = m.values['fshift'] * c_light / lamb
      calerr = m.errors['fshift'] * c_light / lamb
      midpointFTS = np.argmin(np.abs(self.Orders[order]['iow'][iok] - lamb))
      FTSchunk = self.Orders[order]['iow'][iok][midpointFTS + elements/2] - self.Orders[order]['iow'][iok][midpointFTS - elements/2]
      FTSsigma = FTSchunk * m.values['fsigma'] / elements # size of sigma in wavelength
      FWHM = 2.0 * np.sqrt(2.0 * np.log(2.0)) * FTSsigma # size of FWHM in wavelength
      R = lamb / FWHM
      posFTSsigma = FTSsigma + m.errors['fsigma'] / elements # positive error
      negFTSsigma = FTSsigma - m.errors['fsigma'] / elements # negative error
      Rsmall = lamb / (2.0 * np.sqrt(2.0 * np.log(2.0)) * posFTSsigma)
      Rbig = lamb / (2.0 * np.sqrt(2.0 * np.log(2.0)) * negFTSsigma)
      self.fitResults[binSize][order]['bins'][bin]['avwav'] = lamb
      self.fitResults[binSize][order]['bins'][bin]['cal'] = cal
      self.fitResults[binSize][order]['bins'][bin]['calerr'] = calerr
      self.fitResults[binSize][order]['bins'][bin]['R'] = R
      self.fitResults[binSize][order]['bins'][bin]['Rsmall'] = R - Rsmall
      self.fitResults[binSize][order]['bins'][bin]['Rbig'] = Rbig - R
      print datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "finished."
    except:
      # TODO flag bin as bad.
      print "Serious problem with bin:", bin
    pass

  def newbinshiftandtilt(self, order, bin, binSize, fmultiple, fshift, fsigma, elements, fslope, **kwargs):
    """Fit like shift with the addition of a slope across the order."""
    kernel = self.gaussKernel(elements, fsigma)
    ok = self.Orders[order][binSize]['bins'][bin]['ok']
    iok = self.Orders[order][binSize]['bins'][bin]['iok']
    s = si.UnivariateSpline(self.Orders[order]['iow'][iok], np.convolve(kernel, (self.Orders[order]['iof'][iok] * fmultiple) + fslope * (self.Orders[order]['iow'][iok] - np.average(self.Orders[order]['iow'][iok])), mode='same'), s=0)
    overflx = np.array([s.integral(x - self.Orders[order]['mindel']/2.0 + fshift, x + self.Orders[order]['mindel']/2.0 + fshift) for x in self.Orders[order]['wav'][ok]])
    return np.sum( ((overflx - self.Orders[order]['flx'][ok] / self.Orders[order]['con'][ok]) / \
                    (self.Orders[order]['err'][ok] / self.Orders[order]['con'][ok])) ** 2 )

  def saveFIT(self, filename="fit.fits"):
    """docstring for saveFIT"""
    with open(filename, 'wb') as fp:
      pickle.dump(self.fitResults, fp)
    pass

  def loadFIT(self, filename="fit.fits"):
    """docstring for loadFIT"""
    with open(filename, 'rb') as fp:
      self.loadfit = pickle.load(fp)
    pass
  
  # def plotInitialGuess(self, order, fmultiple, fshift, fsigma, elements=1000, sigma=50):
  #   """docstring for plotInitialGuess"""
  #   kernel = self.gaussKernel(elements, fsigma)
  #   tck = si.splrep(self.Orders[order]['oiow'], np.convolve(kernel, self.Orders[order]['oiof'] * fmultiple, mode='same'))
  #   overflx = np.average(si.splev(np.hstack(self.Orders[order]['overwav']) + fshift, tck).reshape(np.shape(self.Orders[order]['overwav'])), axis=1)
  #   pl.plot(self.Orders[order]['wav'], self.Orders[order]['flx'] / self.Orders[order]['con'], color="black", linewidth=2.0)
  #   pl.plot(np.average(self.Orders[order]['overwav'],axis=1), overflx)
  #   pass
  # 
  # def plotFitResults(self, order, fmultiple, fshift, fsigma, elements=1000, **kwargs):
  #   """docstring for plotFitResults"""
  #   kernel = self.gaussKernel(elements, fsigma)
  #   tck = si.splrep(self.Orders[order]['oiow'], np.convolve(kernel, self.Orders[order]['oiof'] * fmultiple, mode='same'))
  #   overflx = np.average(si.splev(np.hstack(self.Orders[order]['overwav']) + fshift, tck).reshape(np.shape(self.Orders[order]['overwav'])), axis=1)
  #   pl.plot(self.Orders[order]['wav'], self.Orders[order]['flx'] / self.Orders[order]['con'], color="black", linewidth=2.0)
  #   pl.plot(np.average(self.Orders[order]['overwav'],axis=1), overflx)    
  #   pass
  # 
  # def plotTiltFitResults(self, order, fmultiple, fshift, fsigma, fslope, elements=1000, plotResiduals=False, **kwargs):
  #   """docstring for plotTiltFitResults"""
  #   kernel = self.gaussKernel(elements, fsigma)
  #   tck = si.splrep(self.Orders[order]['oiow'], np.convolve(kernel, (self.Orders[order]['oiof'] * fmultiple) + fslope * (self.Orders[order]['oiow'] - np.average(self.Orders[order]['oiow'])), mode='same'))
  #   overflx = np.average(si.splev(np.hstack(self.Orders[order]['overwav']) + fshift, tck).reshape(np.shape(self.Orders[order]['overwav'])), axis=1)
  #   pl.plot(self.Orders[order]['wav'], self.Orders[order]['flx'] / self.Orders[order]['con'], color="black", linewidth=2.0)
  #   pl.plot(np.average(self.Orders[order]['overwav'],axis=1), overflx)
  #   if plotResiduals == True:
  #     pl.plot(self.Orders[order]['wav'], self.Orders[order]['flx'] / self.Orders[order]['con'] - overflx, color="red") # data - model
  #   pass
  # 
  # def plotBinTiltFitResults(self, order, fmultiple, fshift, fsigma, fslope, binSize, binNumber, elements=1000, plotResiduals=False, **kwargs):
  #   """docstring for plotBinTiltFitResults"""
  #   kernel = self.gaussKernel(elements, fsigma)
  #   ok = self.Orders[order][binSize]['bins'][binNumber]['ok']
  #   iok = self.Orders[order][binSize]['bins'][binNumber]['iok']
  #   tck = si.splrep(self.Orders[order]['oiow'][iok], np.convolve(kernel, (self.Orders[order]['oiof'][iok] * fmultiple) + fslope * (self.Orders[order]['oiow'][iok] - np.average(self.Orders[order]['oiow'][iok])), mode='same'))
  #   overflx = np.average(si.splev(np.hstack(self.Orders[order]['overwav'][ok]) + fshift, tck).reshape(np.shape(self.Orders[order]['overwav'][ok])), axis=1)
  #   pl.plot(self.Orders[order]['wav'][ok], self.Orders[order]['flx'][ok] / self.Orders[order]['con'][ok], color="black", linewidth=2.0)
  #   pl.plot(np.average(self.Orders[order]['overwav'][ok], axis=1), overflx)
  #   if plotResiduals == True: 
  #     pl.plot(self.Orders[order]['wav'][ok], self.Orders[order]['flx'][ok] / self.Orders[order]['con'][ok] - overflx, color="red")
  #   pass
  # 
  # def plotOrderBinTiltFitResults(self):
  #   """docstring for plotOrderBinTiltFitResults"""
  #   # TODO test for whether order fit; then plot that order
  #   for x in range(7):
  #     ceres.plotBinTiltFitResults(order=7, binNumber=x, **ceres.fitResults[350][7]['bins'][x])
  #   pass
  #   
  # def expplot(self):
  #   """docstring for plot"""
  #   print "working..."
  #   for x in self.Orders:
  #     pl.plot(self.Orders[x]['wav'], self.Orders[x]['flx'])
  #   pl.savefig('ordersinexposure.pdf')
  #   pl.close()
  #   pass
  # 
  # def expftsplot(self):
  #   """docstring for expftsplot"""
  #   for x in self.Orders:
  #     if 'iow' in self.Orders[x]:
  #       pl.plot(self.Orders[x]['wav'], self.Orders[x]['flx'])
  #       pl.plot(self.Orders[x]['iow'], self.Orders[x]['iof'])
  #   pl.savefig('ftsandexposure.pdf')
  #   pl.close()
  #   pass  
  # pass

def main(argv=None):
  pass

if __name__ == "__main__":
  main()