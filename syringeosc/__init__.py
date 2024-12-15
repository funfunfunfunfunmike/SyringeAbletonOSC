from ..abletonosc.osc_server import OSCServer
from typing import Tuple
import logging
import datetime
import Live
import tempfile
import json
import pickle
import os
import time

parameterDefaults = {
    # This Gain used to be "Track_Volume" before I started using utility
    "Track_Volume": .7,
    "Gain": .7,
    "A-Delay": 0,
    "Wet/Dry": 0,
    "1": 0,
    "1/2": 0,
    "1/4": 0,
    "1/8": 0,
    "1/16": 0,
    "1/32": 0,
    "1/64": 0,
    "Sidechain_Duck": 0,
    "High_Pass": 0,
    "Low_Pass": 0,
    "Filter_Resonance": 0,
    "Reverb": 0,
    "Fade_To_Grey": 0,
    "Bit_Reduce": 0,
    "Phaser": 0,
    "Low": .5,
    "Middle": .5,
    "High": .5,
    "Pitch": .5,
    "Timbre": .5,
    "DelayStorm": 0,
    "Trem_1/16": 0,
    "Trem_1/12": 0,
    "Trem_1/8": 0,
    "Trem_Shape": 0,
    "Sat_Mids": 0,
    "Clip_Track_Volume": 0,
    "Master_Track_Volume": .85,
    "Master_Track_Cue_Volume": .8,
    "Cross_Fader": -1,
    "Follow_Song": 1,
    "View": "Detail/Clip"
}

class SyringeOSC:

    def __init__(self, abletonOSCManager):
      
        self.abletonOSCManager = abletonOSCManager
        self.logger = logging.getLogger("abletonosc")
        self.osc_server: OSCServer = self.abletonOSCManager.osc_server
      
        self.listeners = {}
        # When a new track is loaded into a well,
        # we detach listeners from the previously
        # observed clip. This dict takes care of that
        self.clipParameters = {}

        # Holds as many arrays of track parameters
        # as there are FX tracks
        self.trackParameters = []

        # An array of funcs which can't be triggered
        # by Ableton notifications, to be triffered
        # during the next playing status changed cb
        self.deferredFuncs = []

        # Used to ensure we don't send metronome
        # updates too frequently over OSC
        self.lastBeat = -1

        # A track to bpm dictionary for use when we
        # want to fire clips at their default tempos
        # See playClipAtTempo_cb
        self.clipAtTempo = {}

        # Add handlers to the AbletonOSC Manager's OSC
        # server which will call functions in this class
        # when addressed from Engine
        self.addHandlers()

        self.logger.info("Initialized SyringeOSC")
    
    def oscSend(self, *args):
        # Convert bool to int
        toSend = []
        for arg in args:
            if type(arg) == type(True):
                arg = int(arg)
            toSend.append(arg)

        address = "/fromAbleton"
        self.osc_server.send(address, toSend)

    def tick(self):
      """AbletonOSC manager calls this every 100ms"""

      for defFunc in self.deferredFuncs:
        args = defFunc.get("args", [])
        kwargs = defFunc.get("kwargs", {})
        func = defFunc.get("func", None)
        if func is not None:
          func.__call__(*args, **kwargs)
      self.deferredFuncs = []
    
    # ##################################
    # OSC Message Callbacks / Handlers
    #
    # Called when an OSC message from Engine is received
    # ##################################
      
    def addHandlers(self):
        self.osc_server.add_handler("/syringe/registerClips", self.registerClips_cb)
        self.osc_server.add_handler("/syringe/abletonConnect", self.abletonConnect_cb)
        self.osc_server.add_handler("/syringe/pokeTrackParameters", self.pokeTrackParameters_cb)
        self.osc_server.add_handler("/syringe/playClip", self.playClip_cb)
        self.osc_server.add_handler("/syringe/playClipAtTempo", self.playClipAtTempo_cb)
        self.osc_server.add_handler("/syringe/stopTrack", self.stopTrack_cb)
        self.osc_server.add_handler("/syringe/watchSlot", self.watchSlot_cb)
        self.osc_server.add_handler("/syringe/pokeClipParameters", self.pokeClipParameters_cb)
        self.osc_server.add_handler("/syringe/setVolume", self.setVolume_cb)
        self.osc_server.add_handler("/syringe/setTempo", self.setTempo_cb)
        self.osc_server.add_handler("/syringe/setParam", self.setParam_cb)
        self.osc_server.add_handler("/syringe/resetTrack", self.resetTrack_cb)
        self.osc_server.add_handler('/syringe/mute', self.mute_cb)
        self.osc_server.add_handler('/syringe/setPlayingPosition', self.setPlayingPosition_cb)
        self.osc_server.add_handler('/syringe/loop', self.loop_cb)
        self.osc_server.add_handler('/syringe/loopControl', self.loopControl_cb)
        self.osc_server.add_handler('/syringe/loopControlStart', self.loopControlStart_cb)
        self.osc_server.add_handler('/syringe/loopControlEnd', self.loopControlEnd_cb)

    def registerClips_cb(self, params : Tuple):
        """Treated as the main initialization function - this is called
            when requested from the Python Syringe Engine"""
        
        # Register clips
        self.sendClipInfo()
        self.initAbleton()

        # Notify that registration is complete
        self.oscSend("clipsRegistered")

    def abletonConnect_cb(self, params: Tuple):
        clipTracks = getClipTracks()
        fxTracks = getFXTracks()

        self.oscSend("abletonConnectReceived", len(clipTracks), len(fxTracks))

    def pokeTrackParameters_cb(self, params: Tuple):

      track = int(params[0])

      try:
        params = self.trackParameters[track]
      except IndexError:
        return None

      for param in params:
        self.oscSend("trackParamChange", track, makeParamStringID(param),
                    getNormalizedParameterValue(param))

    def playClip_cb(self, params: Tuple):
      track = params[0]
      clip = params[1]
      launchClip(track, clip)

    def playClipAtTempo_cb(self, params: Tuple):
      track = params[0]
      clip = params[1]
      bpm = params[2]
      self.clipAtTempo[canonicalIndex(track)] = bpm
      launchClip(track, clip)

    def stopTrack_cb(self, params : Tuple):
      '''Incoming track is a clip track index'''
      track = params[0]
      respectQuantization = params[1]

      if not respectQuantization:
        # Temporarily set quant to None
        # Try to be in this state for as little time as possible
        old_quant = getSong().clip_trigger_quantization
        song = getSong()
        song.clip_trigger_quantization = Live.Song.Quantization.q_no_q

        # Unquant action
        stopTrack(track)

        # Restore
        song.clip_trigger_quantization = old_quant

      else:
        stopTrack(track)

      # Sometimes, there will be a request to stop a track because
      # it is playing a signal even though there is no clip playing.
      # This can happen if there was audio trapped in a beat repeat,
      # but then the underlying clip reached its end naturally while
      # the beat repeat was left on.  So, here we check and if the
      # track we've been requested to stop is not playing, we reset it
      # which will reset and stop the beat repeat

      # XXX Technically, we would probably want this stop to respect
      # the current quantization settings, but we'd have to schedule it
      # ourselves.  Trying this for now

      cI = canonicalIndex(track)
      aTrack = getClipTrack(cI)

      # No clip playing
      if aTrack.playing_slot_index <= -1:
        self.resetTrack(getFXTrack(cI))

    def watchSlot_cb(self, params : Tuple):
        track = int(params[0])
        clip = int(params[1])
        playAfterWatch = int(params[2])

        clipObject = getClip(track, clip)

        clipListeners = self.clipParameters.get(track, None)

        if clipListeners is not None:

          # If clipListeners was not None, this track previously had a clip
          # being watched, which means it has several listeners. We
          # grab the first one, and take the first item of the tuple, which
          # is the old clip object, and turn RAM mode off. We'll turn RAM
          # mode on for the new clip we're starting to watch below, which
          # is why we must turn it off for the old clip here
          if len(clipListeners) > 0:
            lastClipObject = clipListeners[0][0]
            lastClipObject.ram_mode = False

          for listenerTuple in clipListeners:
            self.removeListenerByName(*listenerTuple)

        # Turn ON ram_mode for newly watched clip
        clipObject.ram_mode = True

        clipListeners = []

        cb = lambda: self.playing_status_change(track, clip, clipObject)
        listenerTuple = (clipObject, "playing_status", cb)
        self.refreshListener(*listenerTuple)
        clipListeners.append(listenerTuple)

        cb = lambda: self.looping_change(track, clip, clipObject)
        listenerTuple = (clipObject, "looping", cb)
        self.refreshListener(*listenerTuple)
        clipListeners.append(listenerTuple)

        cb = lambda: self.playing_position_change(track, clip, clipObject)
        listenerTuple = (clipObject, "playing_position", cb)
        self.refreshListener(*listenerTuple)
        clipListeners.append(listenerTuple)

        cb = lambda: self.start_marker_change(track, clip, clipObject)
        listenerTuple = (clipObject, "start_marker", cb)
        self.refreshListener(*listenerTuple)
        clipListeners.append(listenerTuple)

        cb = lambda: self.end_marker_change(track, clip, clipObject)
        listenerTuple = (clipObject, "end_marker", cb)
        self.refreshListener(*listenerTuple)
        clipListeners.append(listenerTuple)

        cb = lambda: self.loop_start_change(track, clip, clipObject)
        listenerTuple = (clipObject, "loop_start", cb)
        self.refreshListener(*listenerTuple)
        clipListeners.append(listenerTuple)

        cb = lambda: self.loop_end_change(track, clip, clipObject)
        listenerTuple = (clipObject, "loop_end", cb)
        self.refreshListener(*listenerTuple)
        clipListeners.append(listenerTuple)

        self.clipParameters[track] = clipListeners

        if playAfterWatch == 1:
          launchClip(track, clip)

    def pokeClipParameters_cb(self, params : Tuple):
      track = int(params[0])
      clip = int(params[1])

      clipListeners = self.clipParameters.get(track, None)
      if clipListeners == None:
        return None

      for listenerTuple in clipListeners:
        obj = listenerTuple[0]
        prop = listenerTuple[1]
        if hasattr(obj, prop):
          
          # We've got to do some nonsense here to deal with
          # how Ableton reports loop start and loop end if
          # looping is turned off for a clip. According to the
          # LOM docs, the loop_end property is:
          #
          # For looped clips: loop end.
          # For unlooped clips: clip end.
          #
          # This is bad! We want to show the current loop, in
          # case we want to be able to turn looping on while
          # the play position is inside the loop, thus trapping
          # the playback in the loop. Also, we want to be able
          # to modify the loop brace even if looping is off
          #
          # In a way, having the loop brace set before the
          # start marker is a derelict case for Ableton, since
          # it can never be played. Things behave weirdly, but
          # predictably, when this is true, but oddly there is
          # no way to query the loop start/end without turning looping on,
          # and there is no way to not move the start marker when
          # turning looping on, and no way to move it back to a state
          # where it occurs after the loop once its been moved.
          # So, as a convention, I won't set up clips like this any
          # more, and we deal with it by setting a more valid loop
          # 8 beats from the start marker, then turning looping off:
          #
          # Capture current start
          # Turn looping on, which will alter start
          # Set loop end to 8 beats after start
          # Set loop start to old start
          # Set start marker to old start
          # Turn looping off
          #
          # Note that anything unintuitive below is because of loop_end
          # start_marker, loop_start etc returning different things
          # depending on whether looping is on/off.
          
          track = canonicalIndex(getTrack(track))

          if prop == "loop_start":
            start = obj.start_marker
            loop_start = obj.loop_start
            if not obj.looping:
              obj.looping = True
              loop_start = obj.loop_start
              if obj.loop_end < start:
                obj.loop_end = start + 8
                obj.loop_start = start
                loop_start = start
                obj.start_marker = start
              obj.looping = False
            self.oscSend("clipParamChange", track, clip, prop, loop_start)
          elif prop == "loop_end":
            start = obj.start_marker
            end = obj.loop_end
            if not obj.looping:
              obj.looping = True
              # This property is different with loop on/off
              end = obj.loop_end
              if obj.loop_end < start:
                obj.loop_end = start + 8
                end = obj.loop_end
                obj.loop_start = start
                obj.start_marker = start
              obj.looping = False
            self.oscSend("clipParamChange", track, clip, prop, end)
          else:
            propVal = getattr(obj, prop)
            self.oscSend("clipParamChange", track, clip, prop, propVal)

    def setVolume_cb(self, params : Tuple):
      track = int(params[0])
      vol = float(params[1])
      trackVolume(track, vol)

    def setTempo_cb(self, params : Tuple):
      tempo = int(params[0])
      setTempo(tempo)

    def setParam_cb(self, params : Tuple):
      """Track: Sent as val 0-8, refers to FX track, not clip track
          ID: The label of the parameter to control
          Val: Val for param"""
      track = int(params[0])
      id = str(params[1])
      value = float(params[2])

      param = self.getParameterAtTrackByID(track, id)
      setParameterValue(0.0, 1.0, value, param)

    def resetTrack_cb(self, params : Tuple):
      track = int(params[0])
      self.resetTrack(getFXTrack(track))

    def mute_cb(self, params : Tuple):
      track = int(params[0])
      muteval = int(params[1])
      trackO = getFXTrack(track)
      trackO.mute = muteval

    def setPlayingPosition_cb(self, params : Tuple):
      track = int(params[0])
      clip = int(params[1])
      absBeats = int(params[2])

      clip = getClip(track, clip)
      clip.scrub(absBeats)
      clip.stop_scrub()

    def loop_cb(self, params : Tuple):
      track = int(params[0])
      scene = int(params[1])
      loopval = int(params[2])

      clipTrack = getClipTrack(track)

      clip = getClip(absoluteIndex(clipTrack), scene)
      clip.looping = loopval

    def loopControl_cb(self, params : Tuple):

      self.logger.info("In loop control")

      track = int(params[0])
      scene = int(params[1])
      duration = float(params[2])

      clipTrack = getClipTrack(track)
      clip = getClip(absoluteIndex(clipTrack), scene)

      cur_start = clip.loop_start
      cur_end = clip.loop_end
      playpos = clip.playing_position

      if clip.looping == True and playpos >= cur_start and playpos <= cur_end:
        self.logger.info("Alreayd looping and in loop")
        # If clip was already looping, and we're in the loop, extend or
        # shorten the current loop retaining its start time
        clip.loop_end = cur_start + duration
        return

      # Otherwise, we either didn't have looping enabled, or were
      # outside the currently set loop. Let's determine a reasonable
      # start time from the current playing position
      # 
      # See longer comment below for description

      clip.looping = True
      
      startPos = ((int((playpos - 1) // duration)) * duration)

      clip.loop_start = startPos
      clip.loop_end = startPos + duration
      
      # Calculate start position, in beats
      # Effectively, figure out what interval we're in,
      # then figure out the end position of the previous
      # interval, in beats
      #
      # If playpos were 6.67, here are expected results:
      # duration = 4 (1 bar)    -> 5 beats
      # duration = 1 (1 beat)   -> 6 beats
      # duration = 2 (1/2 bar)  -> 5 beats
      # duration = .5 (1/8 bar) -> 6.5 beats

      # Strangely, it seems like the return value of
      # playing position numbers beats like this,

      # |           |           |
      # |  |  |  |  |  |  |  |  |
      # |           |           |
      # 1  2  3  4  5  6  7  8  9
      #

      # But when setting start_pos it's 0 indexed:
      # |           |           |
      # |  |  |  |  |  |  |  |  |
      # |           |           |
      # 0  1  2  3  4  5  6  7  8

      # So we don't add the 1 back in we used to go to
      # 0 indexed beats, so it's
      #
      #   startPos = ((int((playpos-1)/duration))*duration)
      #
      # rather than
      #
      #   startPos = ((int((playpos-1)/duration))*duration)+1
    
    def loopControlStart_cb(self, params : Tuple):
      track = int(params[0])
      scene = int(params[1])
      duration = float(params[2])

      clipTrack = getClipTrack(track)
      clip = getClip(absoluteIndex(clipTrack), scene)

      cur_start = clip.loop_start
      cur_end = clip.loop_end
      playpos = clip.playing_position

      clip.loop_start = cur_start + duration

    def loopControlEnd_cb(self, params : Tuple):
      track = int(params[0])
      scene = int(params[1])
      duration = float(params[2])

      clipTrack = getClipTrack(track)
      clip = getClip(absoluteIndex(clipTrack), scene)

      cur_start = clip.loop_start
      cur_end = clip.loop_end
      playpos = clip.playing_position
      
      clip.loop_end = cur_end + duration

    # ##################################
    # Listener Utilities
    # ##################################

    def addListeners(self):
      self.logger.debug("Adding listeners")

      # Global current time
      self.refreshListener(self.song(), "current_song_time",
                          self.current_song_time_change)

      # Global tempo
      self.refreshListener(self.song(), "tempo", self.tempo_change)

      # Global gain
      self.refreshListener(self.song().master_track, "output_meter_left",
                          self.master_level_change)

      fxTracks = getFXTracks()
      for track in fxTracks:

        cb = lambda track=track: self.output_meter_left_change(track)
        self.refreshListener(track, "output_meter_left", cb)
        cb = lambda track=track: self.output_meter_right_change(track)
        self.refreshListener(track, "output_meter_right", cb)
        cb = lambda track=track: self.volume_change(track)
        self.refreshListener(track.mixer_device.volume, "value", cb)
        cb = lambda track=track: self.solo_change(track)
        self.refreshListener(track, "solo", cb)
        cb = lambda track=track: self.mute_change(track)
        self.refreshListener(track, "mute", cb)

      clipTracks = getClipTracks()
      for track in clipTracks:

        # Observe the fired clip in all clip tracks
        cb = lambda track=track: self.fired_slot_index_change(track)
        self.refreshListener(track, "fired_slot_index", cb)

    def removeListeners(self):
      self.logger.debug("Removing listeners")
      for listenerTuple in list(self.listeners.keys()): 
          self.removeListenerByName(*listenerTuple)

    def refreshListener(self, obj, property, cbFunc):
      self.removeListenerByName(obj, property, cbFunc)
      self.addListenerByName(obj, property, cbFunc)

    def addListenerByName(self, obj, property, cbFunc):
      testStr = "obj.%s_has_listener(cbFunc)" % property
      try:
        test = eval(testStr)
      except:
        self.logger.debug("Couldn't add requested listener:")
        self.logger.debug(obj)
        self.logger.debug(property)
        return
      if test != 1:
        self.logger.debug("Adding by name: %s, %s, %s" % (obj, property, cbFunc.__name__))
        testStr = "obj.add_%s_listener(cbFunc)" % property
        eval(testStr)

        # Add to our list of listeners
        listenerTuple = (obj, property, cbFunc)
        self.listeners[listenerTuple] = True

    def removeListenerByName(self, obj, property, cbFunc):
      testStr = "obj.%s_has_listener(cbFunc)" % property
      try:
        test = eval(testStr)
      except:
        self.logger.debug("Couldn't check has_istener for:")
        self.logger.debug(obj)
        self.logger.debug(property)
        return
      if test == 1:
        self.logger.debug("Removing by name: %s, %s, %s" % (obj, property, cbFunc.__name__))
        testStr = "obj.remove_%s_listener(cbFunc)" % property
        try:
          eval(testStr)
        except:
          self.logger.debug("Couldn't remove requested listener:")
          self.logger.debug(obj)
          self.logger.debug(property)
          return

        # Remove from our list of listeners, if it's in there
        listenerTuple = (obj, property, cbFunc)
        if listenerTuple in self.listeners:
          del self.listeners[listenerTuple]
      else:
        self.logger.debug("No listener found for: %s, %s, %s" % (obj, property, cbFunc))
  
    # ##################################
    # Listeners
    #
    # Called when some state changes in Ableton, usually notifies Engine
    # via OSC
    # ##################################

    def current_song_time_change(self):
      beat = int((self.song().current_song_time % 4) + 1)
      if beat != self.lastBeat:
        self.oscSend("metronome", beat)
        self.lastBeat = beat

    def tempo_change(self):
      tempo = getTempo()
      self.oscSend("tempo", tempo)

    def quick_cue_playing_position_change(self, clip, clipObject):
      self.oscSend("quick_cue_playing_position", clip,
                  clipObject.playing_position)

    def start_marker_change(self, track, clip, clipObject):
      track = canonicalIndex(getTrack(track))
      self.oscSend("start_marker", track, clip, clipObject.start_marker)

    def end_marker_change(self, track, clip, clipObject):
      track = canonicalIndex(getTrack(track))
      self.oscSend("end_marker", track, clip, clipObject.end_marker)

    def loop_start_change(self, track, clip, clipObject):
      self.logger.info("loop start change")
      track = canonicalIndex(getTrack(track))
      # Testing this: only sending loop start or end if looping is enabled,
      # since otherwise, the value is the start marker
      # I could see this causing an issue if edits to the loop brace
      # aren't reported when changes are made with looping off for the clip
      if clipObject.looping:
        self.oscSend("loop_start", track, clip, clipObject.loop_start)

    def loop_end_change(self, track, clip, clipObject):
      self.logger.info("loop end change")
      track = canonicalIndex(getTrack(track))
      # Testing this: only sending loop start or end if looping is enabled,
      # since otherwise, the value is the start marker
      # I could see this causing an issue if edits to the loop brace
      # aren't reported when changes are made with looping off for the clip
      if clipObject.looping:
        self.oscSend("loop_end", track, clip, clipObject.loop_end)
    
    def looping_change(self, track, clip, clipObject):
      """Called when a watched clip's looping status changes"""
      track = canonicalIndex(getTrack(track))
      self.oscSend("looping", track, clip, int(clipObject.looping))
    
    def master_level_change(self):
      level = self.song().master_track.output_meter_left
      self.oscSend("masterLevel", level)

    def output_meter_left_change(self, track):
      self.oscSend("trackLevelLeft", absoluteIndex(track),
                  track.output_meter_left)

    def output_meter_right_change(self, track):
      self.oscSend("trackLevelRight", absoluteIndex(track),
                  track.output_meter_right)

    def volume_change(self, track):
      self.oscSend("trackLevelSetting", absoluteIndex(track),
                  track.mixer_device.volume.value)

    def mute_change(self, track):
      """Called when mute changes on an FX track"""
      self.oscSend("mute", absoluteIndex(track), int(track.mute))

    def solo_change(self, track):
      """Called when solo changes on an FX track"""
      self.oscSend("solo", absoluteIndex(track), int(track.solo))

    def fired_slot_index_change(self, track):
      # Stop will trigger
      if track.fired_slot_index == -2:
        self.oscSend("stopWillTrigger", absoluteIndex(track))
        self.clipAtTempo[canonicalIndex(track)] = None

      if track.fired_slot_index == -1:
        # Clip track has JUST stopped
        if track.playing_slot_index <= -1:
          fxT = clipTrackToFxTrack(track)
          self.deferredFuncs.append({"func": self.resetTrack, "args": [fxT]})
          self.clipAtTempo[canonicalIndex(track)] = None

      # XXX This is where, maybe, you could handle the case
      # of starting playback from the Ableton session view, and
      # then loading it into the applicable well. Shouldn't apply
      # after moving to template track / browser + duplicate.

    def playing_status_change(self, track, clip, clipObject):
      if clipObject.is_triggered == 1:
        self.oscSend("playWillTrigger", track, clip)
      else:
        if clipObject.is_playing == 1:
          self.oscSend("playTriggered", track, clip)
          playAtBPM = self.clipAtTempo.get(canonicalIndex(track), None)
          if playAtBPM is not None:
            self.deferredFuncs.append({
                "func": setTempo,
                "args": [playAtBPM]
            })
            self.clipAtTempo[canonicalIndex(track)] = None
        else:
          self.oscSend("clipStopped", track, clip)
          self.clipAtTempo[canonicalIndex(track)] = None

    def playing_position_change(self, track, clip, clipObject):
      track = canonicalIndex(getTrack(track))
      #end = float(clipObject.end_marker)
      #pos = clipObject.playing_position / end
      # Send the playing position in beats
      self.oscSend("playing_position", track, clip, clipObject.playing_position)

    # ##################################
    # Ableton function and logic
    # ##################################
    
    def song(self):
       return getSong()
    
    def resetTrack(self, fxTrack):
      try:
        params = self.trackParameters[absoluteIndex(fxTrack)]
      except IndexError:
        return

      # Reset all rack params
      for param in params:
        id = makeParamStringID(param)
        default = parameterDefaults.get(id, None)
        if default is not None:
          setParameterValue(0.0, 1.0, default, param)

      clipTrack = fxTrackToClipTrack(fxTrack)

      # Unmute
      fxTrack.mute = 0
      clipTrack.mute = 0

      # Unsolo
      # 6/24/22 Turning this off as part of resetting
      # a track, since I was using solo-ing as part of
      # the QuickCue system
      #fxTrack.solo = 0
      #clipTrack.solo = 0

      # Disarm
      if fxTrack.can_be_armed:
        fxTrack.arm = 0
      if clipTrack.can_be_armed:
        clipTrack.arm = 0

      # Unassign A/B crossfade
      fxTrack.mixer_device.crossfade_assign = 1
      clipTrack.mixer_device.crossfade_assign = 1

      clipTrack.mixer_device.volume.value = parameterDefaults["Clip_Track_Volume"]

    def resetAllTracks(self):
      """Resets all clip tracks"""
      fxTracks = getFXTracks()
      for track in fxTracks:
        self.resetTrack(track)

      master = self.song().master_track
      master.mixer_device.volume.value = parameterDefaults["Master_Track_Volume"]
      master.mixer_device.crossfader.value = parameterDefaults["Cross_Fader"]
      master.mixer_device.cue_volume.value = parameterDefaults[
          "Master_Track_Cue_Volume"]

      setFollow(parameterDefaults["Follow_Song"])
      showView(parameterDefaults["View"])

    def getParameterAtTrackByID(self, track, id):
      try:
        params = self.trackParameters[track]
      except IndexError:
        return None

      for param in params:
        if self.makeParamStringID(param) == id:
          return param

    # ##################################
    # Clip and session registration
    # ##################################

    def sendClipInfo(self):

      t1 = time.time()

      # Adding QuickCueTrack, though we will not include this in the session
      tracks = getClipTracks() + [ getQuickCueTrack() ]

      if len(tracks) == 0:
          return

      # It is taking absolutely forever to actually
      # store the roughly 5400 API objects.  So, what we
      # do is scan tracks until we find a clip track.
      # We then loop through those and send out registration
      # notices for all clip tracks.  
      # 
      # This assumes all other
      # clip tracks have identical clips.

      # Sending a message for each clip registration, I was losing
      # UDP packets which was causing horrible frustrating bugs.
      # To fix that, I wanted to batch and send the whole session.
      # A session file for about 600 clips per track
      # is about 127kb.
      # After testing (6/30/13), oscSend has a max packet size
      # of 9172 bytes, after which is just stalls and doesn't send.
      # So, I'm accumulating the session as a list of lists, pickilng
      # it to a temp file, and sending a message with a path to the
      # temp file.

      templateTrack = tracks[0]

      # Adding the -1 to not account for quick cue in session
      session = [[] for x in range(len(tracks)-1)]

      t2 = time.time()
      clipslots = [x.clip_slots for x in tracks]

      for cID, clipslot in enumerate(templateTrack.clip_slots):
          clip = clipslot.clip
          
          if clip is not None:
            
              # If we encountered a human named or otherwise
              # unparseable json clip name, give it a default
              try:
                  json.loads(clip.name)
              except ValueError:
                  defaultName = (
                                '{{"name": "{name}", '
                                '"artist": "", '
                                '"album": "", '
                                '"year": {year}, '
                                '"dateAdded": "{today}", '
                                '"path": "{filepath}", '
                                '"bpm": {bpm}, '
                                '"key": ["#"], '
                                '"cuePoints": [], '
                                '"loop": {loop}, '
                                '"categories": [], '
                                '"desc": ""}}'
                  ).format(
                      name = clip.name, 
                      year = datetime.datetime.now().year, 
                      today = datetime.datetime.now().strftime("%m/%d/%Y"), 
                      filepath = clip.file_path, 
                      bpm = int(getTempo()), 
                      loop = str(clip.looping).lower()
                  )

                  clip.name = defaultName

              for v, track in enumerate(tracks):

                  # If we're looking at a track other than template
                  # track, let's do some verification.
                  # XXX These checks, I think because of accessing each clip slot
                  # in all the additional tracks, add more than 1 second to clip registration,
                  # which makes app startup seem sluggish. These checks go away completely if
                  # we move to having a template track only and duplicating clips as needed

                  if not track == templateTrack:
                    
                    # Check that there is a clip here - there may not be
                    # if I just added a new clip to the template track
                    if track.clip_slots[cID].clip == None:
                      self.logger.info("Copying clips")
                      templateTrack.clip_slots[cID].duplicate_clip_to(track.clip_slots[cID])

                    # Check that the name of the clip in this slot is identical
                    # to the template track
                    examine_clip = track.clip_slots[cID].clip
                    if examine_clip.name != clip.name:
                      self.logger.info("Found clip mismatching template track name: {c}, on clip track {track}".format(c=examine_clip.name, track=v))
                      examine_clip.name = clip.name

                  name = clip.name
                  file_path = clip.file_path
                  warpmarks = []

                  for warpmarker in clip.warp_markers:
                    warpmarks.append([warpmarker.beat_time, warpmarker.sample_time])

                  samplelength_sec = float(clip.sample_length) / float(clip.sample_rate)

                  # See above. Not including quick cue in session
                  if track != getQuickCueTrack():

                    tID = absoluteIndex(track)
                  
                    session[v].append({
                        "track": tID,
                        "clip": cID,
                        "warpmarkers" : warpmarks,
                        "samplelength" : samplelength_sec,
                        "name": name.encode('utf-8'),
                        "filepath": file_path.encode('utf-8')
                    })

      sessionFile = pickleToTempFile(session)
      self.oscSend("clipsReady", sessionFile)

      self.logger.info("Registered clips in: {elapsed_time:.2f} seconds".format(elapsed_time=time.time() - t1))

    def initAbleton(self):
      self.removeListeners()
      self.addListeners()
      
      # Register params
      self.registerParams()

      # Reset all tracks
      self.resetAllTracks()

      # Send over current tempo
      self.tempo_change()

      # Ensure global quantization is at one bar
      self.song().clip_trigger_quantization = Live.Song.Quantization.q_bar

      # Ensure the Quick Cue track is cued
      soloTrack(getQuickCueTrack())
      muteTrack(getQuickCueTrack())

      # Ensure cue master track, sending master to cue output, is cued
      # XXX and unmuted?
      enableCueMaster()

      # Send over current gain level of all FX tracks
      tracks = getFXTracks()
      for track in tracks:
          self.oscSend("trackLevelSetting", absoluteIndex(track),
                      track.mixer_device.volume.value)

      clipTracks = getClipTracks()
      for ctrack in clipTracks:
         slot = ctrack.playing_slot_index
         # check if there is a clip playing on each track
         # if so, sent its slot to engine to load into well new msg 
         if slot > -1:
           self.oscSend("loadWellWithClip", absoluteIndex(ctrack), slot)

    def track_parameter_change(self, track, param):
      self.oscSend("trackParamChange", track, makeParamStringID(param),
                  getNormalizedParameterValue(param))

    def registerTrackParam(self, array, track, deviceParam):
      cb = lambda track=track, param=deviceParam: self.track_parameter_change(
          track, param)
      self.refreshListener(deviceParam, "value", cb)
      array.append(deviceParam)

      # Send out the current and initial value of this param
      self.oscSend("trackParamChange", track, makeParamStringID(deviceParam),
                  getNormalizedParameterValue(deviceParam))

    def registerParams(self):
      self.trackParameters = []
      fxTracks = getFXTracks()
      for fxTrackNum, track in enumerate(fxTracks):
        parmArray = []

        # Track Volume

        # Commenting this out for now (6.2.23) because I'm using utility gain
        # may need! May bring back
        
        # ^^ Temporarily turning on again (12.11.24) because I noticed vol gain
        # hasn't been resetting when I DJ on the laptop without the syringe. Longer
        # term fix is use gain for this one as well.
        self.registerTrackParam(parmArray, fxTrackNum, track.mixer_device.volume)
        # You can find the new way we track volume after the racks

        # Track Send One
        self.registerTrackParam(parmArray, fxTrackNum,
                                track.mixer_device.sends[0])

        # FX Wet / Dry
        self.registerTrackParam(parmArray, fxTrackNum,
                                track.devices[0].parameters[1])

        # The following three loops pull from the WET chain (chain 1)
        # of the Plustype Well Rack chain device.  The WET chain
        # contains three child devices
        # Beat repeat 1
        # Beat repeat 1/2
        # Beat repeat 1/4
        # Beat repeat 1/8
        # Beat repeat 1/16
        # Beat repeat 1/32
        # Beat repeat 1/64
        # Empty macro spot 8, not processed
        for paramIndex in range(1, 8):
          self.registerTrackParam(
              parmArray, fxTrackNum,
              track.devices[0].chains[1].devices[0].parameters[paramIndex])

        # Sidechain duck
        # High pass
        # Low pass
        # Filter resonance
        # Reverb
        # Fade to grey
        # Bit reduce
        # Phaser
        for paramIndex in range(1, 9):
          self.registerTrackParam(
              parmArray, fxTrackNum,
              track.devices[0].chains[1].devices[1].parameters[paramIndex])

        # Pitch
        # Timbre
        # DelayStorm
        # Trem_1/16
        # Trem_1/12
        # Trem_1/8
        # Trem_Shape
        # Sat_Mids
        for paramIndex in range(1, 9):
          self.registerTrackParam(
              parmArray, fxTrackNum,
              track.devices[0].chains[1].devices[2].parameters[paramIndex])

        # EQ Low
        # EQ Middle
        # EQ High
        for paramIndex in range(1, 4):
          self.registerTrackParam(
              parmArray, fxTrackNum,
              track.devices[0].chains[1].devices[3].parameters[paramIndex])

        # XXX Now using utility gain rather than the track mixer volume,
        # because if I use track mixer volume, we can't hear the track
        # in cueing
        self.registerTrackParam(parmArray, fxTrackNum,
                                track.devices[1].parameters[1])

        self.trackParameters.append(parmArray)

###################################################
###################################################
# Utils brought over from LiveUtils
###################################################
###################################################

def getClipTracks():
  return [x for x in getTracks() if isTrackClipTrack(x)]

def getFXTracks():
  return [x for x in getTracks() if isTrackFXTrack(x)]

def getFXTrack(num):
  num = int(num)
  return getFXTracks()[num]

def getClipTrack(num):
  num = int(num)
  return getClipTracks()[num]

def getTracks():
  """Returns a list of tracks"""
  return getSong().visible_tracks

def getTrack(num):
  """Returns track number (num) (starting at 0)"""
  return getSong().visible_tracks[num]

def getSong():
  """Gets a the current Song instance"""
  return Live.Application.get_application().get_document()

def isTrackFXTrack(track):
  return track.name.endswith("FX")

def isTrackClipTrack(track):
  return track.name.endswith("CLIPS")

def absoluteIndex(track):
  """Passed an FX track or Clip track, returns its absolute index 0-17.

    This is replacing the concept of "ptIndex" that used to be added pre
    Live 9 as an ad-hoc attribute on tracks in getTracks. That attribute
    seems to be getting stripped somehow, so now we make this computable
    from the track name."""

  canonindex = int(track.name.split("_")[0]) - 1
  if isTrackFXTrack(track):
    return canonindex
  else:
    # +10 b/c the clip tracks are grouped and the group
    # counts as a track
    return canonindex + 10

def canonicalIndex(track):
  """Passed an FX track or Clip track (or int), returns its canonical
    index (0-8)"""

  # XXX This could just be implemented by splitting on the track
  # name. isFX and isClip track both rely on naming...

  if type(track) == type(2):
    track = getTrack(track)

  index = 0
  for ct, fxt in zip(getClipTracks(), getFXTracks()):
    if track == ct or track == fxt:
      return index
    index += 1

def pickleToTempFile(obj):
    """Dumps obj to a temp file and returns a path to that file"""
    t = tempfile.mkstemp()
    f = os.fdopen(t[0], 'w')
    p = pickle.dumps(obj)
    f.write(p)
    f.close()
    return t[1]

def pickleToTempFile(obj):
    """Dumps obj to a temp file and returns a path to that file"""
    t = tempfile.mkstemp()
    # Open the file descriptor in binary write mode
    with os.fdopen(t[0], 'wb') as f:
        p = pickle.dumps(obj)
        f.write(p)
    # Return the path to the temporary file
    return t[1]

def getSong():
  """Gets a the current Song instance"""
  return Live.Application.get_application().get_document()

def getTempo():
  """Returns the current song tempo"""
  return getSong().tempo

def clipTrackToFxTrack(clipTrack):
  """Passed a clip track, returns the associated FX track."""
  cliptracks = getClipTracks()
  fxtracks = getFXTracks()
  for clipT, fxT in zip(cliptracks, fxtracks):
    if clipT == clipTrack:
      return fxT
    
def fxTrackToClipTrack(fxTrack):
  """Passed an FX track, returns the associated clip track."""
  cliptracks = getClipTracks()
  fxtracks = getFXTracks()
  for clipT, fxT in zip(cliptracks, fxtracks):
    if fxT == fxTrack:
      return clipT

def makeParamStringID(param):
    """Get a canonical string ID to represent this
        parameter."""
    name = param.name
    nameRep = str(name)
    nameRep = nameRep.replace(" ", "_")
    return nameRep

def setParameterValue(inMin, inMax, value, param):
  newRangeMin = param.min
  newRangeMax = param.max
  param.value = scaleValue(inMin, inMax, newRangeMin, newRangeMax, value)

def scaleValue(oldRangeMin, oldRangeMax, newRangeMin, newRangeMax, value):
  oldWidth = oldRangeMax - oldRangeMin
  newWidth = newRangeMax - newRangeMin
  return newRangeMin + (((value - oldRangeMin) / oldWidth) * newWidth)

def getNormalizedParameterValue(deviceParam):
  oldRangeMin = deviceParam.min
  oldRangeMax = deviceParam.max
  newRangeMin = 0.0
  newRangeMax = 1.0
  value = deviceParam.value
  return scaleValue(oldRangeMin, oldRangeMax, newRangeMin, newRangeMax, value)

def setFollow(follow):
  follow = int(follow)
  getSong().view.follow_song = follow

def showView(view):
  Live.Application.get_application().view.show_view(view)


def soloTrack(track):
  """Solo track"""
  for ct in getTracks():
    if ct == track:
      ct.solo = 1

def muteTrack(track):
  """Mutes track"""
  for ct in getTracks():
    if ct == track:
      ct.mute = 1

def getQuickCueTrack():
  return getTrackByName("QUICK_CUE")

def getTrackByName(search_name):
  """Returns track with name search_name, or None if not found"""
  for t in getTracks():
    if t.name == search_name:
      return t
  return None

# Tracks for sending master to cue output
# These are all the CUE'd FX tracks, and instruments
def getCueMasterTracks():
  cueTracks = getFXTracks()
  cueTracks.extend([getTrackByName("INST")])
  cueTracks.extend([getTrackByName("TWISTER_STEP_SEQ")])
  #cueTracks.extend([getTrackByName("VST_VOCAL")])
  #cueTracks.extend([getTrackByName("TALKOVER")])
  return cueTracks

def enableCueMaster():
  tracks = getCueMasterTracks()
  for track in tracks:
    soloTrack(track)

def launchClipAtTempo(track, clip, bpm):
  """Launches clip numer (clip) in track number (track)
    at bpm (bpm)"""
  setTempo(bpm)
  launchClip(track, clip)

def launchClip(track, clip):
  """Launches clip number (clip) in track number (track)"""
  # Trying design idea - at launch, check if there is anything playing.
  # If not, ignore quantization and start sound immediately. Otherwise,
  # respect quantization
  # I could imagine a rare case where a clip has stopped, and is still going
  # rhytmically in a beat repeat or delay, and this being off the clock, but
  # seems worth the tradeoff..

  # This would only be bad if that caused an unexpected dropout when something
  # was looping. Seems rare.

  # Count clip tracks with currently playing track
  playingTracks = 0
  clipTracks = getClipTracks()
  for cctrack in clipTracks:
    if cctrack.playing_slot_index > -1:
      playingTracks += 1
      
  if playingTracks == 0:
    # Temporarily set quant to None
    song = getSong()
    song.stop_playing()
    old_quant = song.clip_trigger_quantization
    song.clip_trigger_quantization = Live.Song.Quantization.q_no_q

  # Unquant action
  getClip(track, clip).fire()

  if playingTracks == 0:
    # Restore
    song.clip_trigger_quantization = old_quant

def getClip(track, clip):
  """Returns clip number (clip) in track (track)"""
  return getSong().visible_tracks[track].clip_slots[clip].clip

def setTempo(tempo):
  getSong().tempo = tempo

def stopTrack(trackNumber):
  """Stops all clips in track number (trackNumber)"""
  track = getTrack(trackNumber)
  if track is not None:
    track.stop_all_clips()

def trackVolume(track, volume=None):
  """Gets/Changes the volume of track (track)

    If (volume) is specified, changes the volume of track number
    (track) to (volume), a value between 0.0 and 1.0.
    """
  if volume != None:
    getTrack(track).mixer_device.volume.value = volume
  return getTrack(track).mixer_device.volume.value

def selectScene(scene):
  scene = int(scene)
  getSong().view.selected_scene = getScene(scene)

def getScenes():
  """Returns a list of scenes"""
  return getSong().scenes

def getScene(num):
  """Returns scene number (num) (starting at 0)"""
  return getSong().scenes[num]

def getTempo():
  """Returns the current song tempo"""
  return getSong().tempo