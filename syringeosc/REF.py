# execfile("/Users/mtf/Desktop/hackshit.py", globals(), locals())
# Can use this to prototype since > Live 8 we have to restart live
# to reload Python RemoteScript code, which is irritating

import Live
import LiveUtils
import RemixNet
import OSC
import sys
from StringIO import StringIO
import tempfile
import os
import pickle
from Logger import log
import time
import traceback_live as traceback
from _Framework.ControlSurface import ControlSurface

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


class PlustypeControl(ControlSurface):

  def __init__(self, c_instance):
    ControlSurface.__init__(self, c_instance)

    self.listeners = {}
    # When a new track is loaded into a well,
    # we detach listeners from the previously
    # observed clip. This dict takes care of that
    self.clipParameters = {}

    self.oscEndpoint = None
    self.songReady = False

    # Holds as many arrays of track parameters
    # as there are FX tracks
    self.trackParameters = []

    # An array of funcs which can't be triggered
    # by Ableton notifications, to be triffered
    # during the next playing status changed cb
    self.deferredFuncs = []

    # An array of funcs we try to call in sync
    # with global quantization
    # This is just awful. I'm not using it for
    # anything.
    self.quantizedFuncs = []

    # A track to bpm dictionary for use when we
    # want to fire clips at their default tempos
    # See playClipAtTempo_cb
    self.clipAtTempo = {}

    # Used to ensure we don't send metronome
    # updates too frequently over OSC
    self.lastBeat = -1

    self.interpreterLocals = {}

    self.ptShow("Plustype Syringe Control loaded")

  # ##################################
  # Other
  # ##################################

  def scaleValue(self, oldRangeMin, oldRangeMax, newRangeMin, newRangeMax,
                 value):
    oldWidth = oldRangeMax - oldRangeMin
    newWidth = newRangeMax - newRangeMin
    return newRangeMin + (((value - oldRangeMin) / oldWidth) * newWidth)

  def getNormalizedParameterValue(self, deviceParam):
    oldRangeMin = deviceParam.min
    oldRangeMax = deviceParam.max
    newRangeMin = 0.0
    newRangeMax = 1.0
    value = deviceParam.value
    return self.scaleValue(oldRangeMin, oldRangeMax, newRangeMin, newRangeMax,
                           value)

  def getParameterAtTrackByID(self, track, id):
    try:
      params = self.trackParameters[track]
    except IndexError:
      return None

    for param in params:
      if self.makeParamStringID(param) == id:
        return param

  def setParameterValue(self, inMin, inMax, value, param):
    newRangeMin = param.min
    newRangeMax = param.max
    param.value = self.scaleValue(inMin, inMax, newRangeMin, newRangeMax, value)

  def sendClipInfo(self):
    tracks = LiveUtils.getClipTracks()

    if len(tracks) == 0:
      return

    # It is taking absolutely forever to actually
    # store the roughly 5400 API objects.  So, what we
    # do is scan tracks until we find a clip track.
    # We then loop through those and send out registration
    # notices for all clip tracks.  This assumes all other
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
    session = [[] for x in xrange(len(tracks))]

    for cID, clipslot in enumerate(templateTrack.clip_slots):
      clip = clipslot.clip
      if clip is not None:

        for v, track in enumerate(tracks):
          name = clip.name

          # file_path property was added in Live9
          if LiveUtils.LiveVersion() >= 9:
            file_path = clip.file_path
          else:
            file_path = ""

          tID = LiveUtils.absoluteIndex(track)
          session[v].append({
              "track": tID,
              "clip": cID,
              "name": name.encode('utf-8'),
              "filepath": file_path.encode('utf-8')
          })

    sessionFile = self.pickleToTempFile(session)
    self.oscSend("clipsReady", sessionFile)

  def pickleToTempFile(self, obj):
    """Dumps obj to a temp file and returns a path to that file"""
    t = tempfile.mkstemp(text=True)
    f = os.fdopen(t[0], 'w')
    p = pickle.dumps(obj)
    f.write(p)
    f.close()
    return t[1]

  def resetAllTracks(self):
    """Resets all clip tracks"""
    fxTracks = LiveUtils.getFXTracks()
    for track in fxTracks:
      self.resetTrack(track)

    master = self.song().master_track
    master.mixer_device.volume.value = parameterDefaults["Master_Track_Volume"]
    master.mixer_device.crossfader.value = parameterDefaults["Cross_Fader"]
    master.mixer_device.cue_volume.value = parameterDefaults[
        "Master_Track_Cue_Volume"]

    LiveUtils.setFollow(parameterDefaults["Follow_Song"])
    LiveUtils.showView(parameterDefaults["View"])

  def resetTrack(self, fxTrack):
    try:
      params = self.trackParameters[LiveUtils.absoluteIndex(fxTrack)]
    except IndexError:
      return

    # Reset all rack params
    for param in params:
      id = self.makeParamStringID(param)
      default = parameterDefaults.get(id, None)
      if default is not None:
        self.setParameterValue(0.0, 1.0, default, param)

    clipTrack = LiveUtils.fxTrackToClipTrack(fxTrack)

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

  def track_parameter_change(self, track, param):
    self.oscSend("trackParamChange", track, self.makeParamStringID(param),
                 self.getNormalizedParameterValue(param))

  def registerTrackParam(self, array, track, deviceParam):
    cb = lambda track=track, param=deviceParam: self.track_parameter_change(
        track, param)
    self.refreshListener(deviceParam, "value", cb)
    array.append(deviceParam)

    # Send out the current and initial value of this param
    self.oscSend("trackParamChange", track, self.makeParamStringID(deviceParam),
                 self.getNormalizedParameterValue(deviceParam))

  def registerParams(self):
    self.trackParameters = []
    fxTracks = LiveUtils.getFXTracks()
    for fxTrackNum, track in enumerate(fxTracks):
      parmArray = []

      # Track Volume

      # Commenting this out for now (6.2.23) because I'm using utility gain
      # may need! May bring back
      #self.registerTrackParam(parmArray, fxTrackNum, track.mixer_device.volume)
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

      # Registering the mtf pitch detection M4L device
      # Path from explorer:
      # path live_set tracks 0 devices 2 parameters 1
      self.registerTrackParam(parmArray, fxTrackNum,
                              track.devices[2].parameters[1])
      self.registerTrackParam(parmArray, fxTrackNum,
                              track.devices[2].parameters[2])

      self.trackParameters.append(parmArray)

  def makeParamStringID(self, param):
    """Get a canonical string ID to represent this
        parameter."""
    name = param.name
    nameRep = str(name)
    nameRep = nameRep.replace(" ", "_")
    return nameRep

  # ##################################
  # OSC Server related
  # ##################################

  def initOSC(self):
    """Starts or restarts OSC server"""
    if self.oscEndpoint is not None:
      self.oscEndpoint.shutdown()
      del self.oscEndpoint
    self.oscEndpoint = RemixNet.OSCEndpoint()
    self.addCallbacks()
    self.ptLog("Completed OSC init")

  def oscSend(self, *args):
    """Sends the OSC message to the Syringe controller Python app"""
    if self.oscEndpoint is None:
      self.initOSC()

    # Convert bool to int
    toSend = []
    for arg in args:
      if type(arg) == type(True):
        arg = int(arg)
      toSend.append(arg)

    address = "/fromAbleton"
    self.oscEndpoint.send(address, toSend)

  # ##################################
  # Callbacks (Run on OSC messages received from Engine)
  # ##################################

  def addCallbacks(self):
    cbm = self.oscEndpoint.callbackManager
    cbm.add("/setParam", self.setParam_cb)
    cbm.add("/setTempo", self.setTempo_cb)
    cbm.add("/registerClips", self.registerClips_cb)
    cbm.add("/playClip", self.playClip_cb)
    cbm.add("/playClipAtTempo", self.playClipAtTempo_cb)
    cbm.add("/stopTrack", self.stopTrack_cb)
    cbm.add("/selectTrack", self.selectTrack_cb)
    cbm.add("/selectClipTrack", self.selectClipTrack_cb)
    cbm.add("/selectScene", self.selectScene_cb)
    cbm.add("/watchSlot", self.watchSlot_cb)
    cbm.add("/setVolume", self.setVolume_cb)
    cbm.add("/cue", self.cue_cb)
    cbm.add("/solo", self.solo_cb)
    cbm.add("/exclusiveSolo", self.exclusiveSolo_cb)
    cbm.add("/mute", self.mute_cb)
    cbm.add("/loop", self.loop_cb)
    cbm.add("/loopControl", self.loopControl_cb)
    cbm.add("/loopControlDuration", self.loopControlDuration_cb)
    cbm.add("/pokeTrackParameters", self.pokeTrackParameters_cb)
    cbm.add("/pokeClipParameters", self.pokeClipParameters_cb)
    cbm.add("/showDetailClip", self.showDetailClip_cb)
    cbm.add("/resetTrack", self.resetTrack_cb)
    cbm.add("/watchSelectionChange", self.watchSelectionChange_cb)
    cbm.add("/renameSelectedClip", self.renameSelectedClip_cb)
    cbm.add("/addBeatportSongByID", self.addBeatportSongByID_cb)
    cbm.add("/setPlayingPosition", self.setPlayingPosition_cb)
    cbm.add("/setClipStartMarker", self.setClipStartMarker_cb)
    cbm.add("/quickCueEnable", self.quickCueEnable_cb)
    cbm.add("/quickCueScrub", self.quickCueScrub_cb)
    cbm.add("/playPreSeqCue", self.playPreSeqCue_cb)
    cbm.add("/abletonConnect", self.abletonConnect_cb)

    cbm.add("/execCode", self.execCode_cb)

  def abletonConnect_cb(self, msg, source):
    clipTracks = LiveUtils.getClipTracks()
    fxTracks = LiveUtils.getFXTracks()

    self.oscSend("abletonConnectReceived", len(clipTracks), len(fxTracks))

  def playPreSeqCue_cb(self, msg, source):
    clipName = str(msg[2])

    psCue = LiveUtils.getTrackByName("PRE_SEQ_CUE")
    if psCue is not None:
      clip = LiveUtils.getClipByName(psCue, clipName)
      if clip is not None:
        clip.fire()

  def quickCueEnable_cb(self, msg, source):
    scene = msg[2]
    enable = msg[3]

    quickCue_track = LiveUtils.getQuickCueTrack()
    if quickCue_track is None:
      self.ptLog("Could not find Quick Cue track...")
      return

    #### Handle watching quick cue'd clip
    tracks = LiveUtils.getTracks()
    # My clip track and canonical thing doesn't really
    # properly allow for quick cue, so searching for it's abs
    # index myself
    found = False
    quick_cue_track_index = 0
    for t in tracks:
      if t == quickCue_track:
        found = True
        break
      quick_cue_track_index += 1

    if not found:
      self.ptLog("Could not find Quick Cue track...")
      return

    slot = quickCue_track.clip_slots[scene]
    clip = slot.clip

    # In quick cue enable, we disable routing master to the cue headphones
    # whereas when we cue with well cueing, master IS routed so we
    # can try mixes out
    if enable == True:

      clipListeners = self.clipParameters.get(quick_cue_track_index, None)

      if clipListeners is not None:
        # If clipListeners was not None, this track previously had a clip
        # being watched, which means it has several listeners. Remove them.
        if len(clipListeners) > 0:
          lastClipObject = clipListeners[0][0]
          lastClipObject.ram_mode = False

        for listenerTuple in clipListeners:
          self.removeListenerByName(*listenerTuple)

      clipListeners = []

      cb = lambda: self.quick_cue_playing_position_change(scene, clip)
      listenerTuple = (clip, "playing_position", cb)
      self.refreshListener(*listenerTuple)
      clipListeners.append(listenerTuple)

      self.clipParameters[quick_cue_track_index] = clipListeners

      # Actually get things going

      # FX tracks 1-9
      # INST
      # TWISTER_STEP_SEQ
      LiveUtils.disableCueMaster()
      LiveUtils.soloTrack(LiveUtils.getQuickCueTrack())

      clip.ram_mode = True

      # Temporarily set quant to None
      # Try to be in this state for as little time as possible
      old_quant = LiveUtils.getSong().clip_trigger_quantization
      song = LiveUtils.getSong()
      song.clip_trigger_quantization = Live.Song.Quantization.q_no_q

      clip.fire()

      # Restore
      song.clip_trigger_quantization = old_quant

    elif enable == False:

      clipListeners = self.clipParameters.get(quick_cue_track_index, None)

      if clipListeners is not None:
        # If clipListeners was not None, this track previously had a clip
        # being watched, which means it has several listeners. Remove them.
        if len(clipListeners) > 0:
          lastClipObject = clipListeners[0][0]
          lastClipObject.ram_mode = False

        for listenerTuple in clipListeners:
          self.removeListenerByName(*listenerTuple)

      # In quick cue disable, we renable routing master to cue out
      LiveUtils.soloTrack(LiveUtils.getQuickCueTrack())
      LiveUtils.enableCueMaster()

      # Temporarily set quant to None
      # Try to be in this state for as little time as possible
      old_quant = LiveUtils.getSong().clip_trigger_quantization
      song = LiveUtils.getSong()
      song.clip_trigger_quantization = Live.Song.Quantization.q_no_q

      clip.stop()

      # Restore
      song.clip_trigger_quantization = old_quant

      clip.ram_mode = False

  def quickCueScrub_cb(self, msg, source):
    scene = msg[2]
    bars = msg[3]

    quickCue_track = LiveUtils.getQuickCueTrack()

    slot = quickCue_track.clip_slots[scene]
    clip = slot.clip

    new_beat = clip.playing_position + bars
    if new_beat < 0:
      new_beat = 0
    elif new_beat >= clip.end_marker:
      new_beat = clip.end_marker - 8

    old_quant = LiveUtils.getSong().clip_trigger_quantization
    song = LiveUtils.getSong()
    # Temporarily set quant to None
    song.clip_trigger_quantization = Live.Song.Quantization.q_no_q
    clip.scrub(new_beat)
    clip.stop_scrub()
    # Restore
    song.clip_trigger_quantization = old_quant

  def setClipStartMarker_cb(self, msg, source):

    # Third param assumed to be in beats since all my clips are warped
    # If clips aren't warped, set start marker in Ableton goes to seconds
    # I do have some unwarped one shot samples, but cue points are ill-defined
    # for those, anyway

    track = msg[2]
    scene = msg[3]
    startBeat = msg[4]

    # For some fucking Abletony reason, loop has to
    # be on to change the start marker
    # So we can turn it right off after if necessary

    clip = LiveUtils.getClip(track, scene)

    oldLoopState = clip.looping

    if oldLoopState == False:
      clip.looping = True

    # Now we can make modifications
    # This will fail if the new start
    # is before the end, so just for the hell of it,
    # put the end marker 8 bars after the desired start marker

    if clip.end_marker <= startBeat:
      clip.end_marker = startBeat + 32
      self.ptLog(
          "Had to move end marker to accommodate cue point start change request"
      )

    clip.start_marker = startBeat

    if oldLoopState == False:
      clip.looping = False

  def setPlayingPosition_cb(self, msg, source):

    track = msg[2]
    clip = msg[3]
    absBeats = msg[4]

    clip = LiveUtils.getClip(track, clip)
    clip.scrub(absBeats)
    clip.stop_scrub()

  def renameSelectedClip_cb(self, msg, source):
    newName = str(msg[2])
    LiveUtils.setDetailClipName(newName)

  def setTempo_cb(self, msg, source):
    tempo = int(msg[2])
    LiveUtils.setTempo(tempo)

  def addBeatportSongByID_cb(self, msg, source):

    bp_id = int(msg[2])
    newName = str(msg[3])

    def selectNextEmptySlotOnTemplateTrack():
      tracks = LiveUtils.getClipTracks()
      templateTrack = tracks[0]

      # Create a new scene if there is no empty
      if templateTrack.clip_slots[len(templateTrack.clip_slots) -
                                  1].clip != None:
        LiveUtils.getSong().create_scene(-1)

      # Intentionally starting 1 too high on this counter
      sc_index = len(templateTrack.clip_slots)
      for cs in reversed(templateTrack.clip_slots):
        if cs.clip != None or sc_index == 0:
          # Found an empty clip slot for insertion
          break
        sc_index -= 1

      LiveUtils.getSong().view.selected_track = templateTrack
      LiveUtils.selectScene(sc_index)

      return templateTrack.clip_slots[sc_index]

    def getBrowserItemForID(bp_id):
      browser = Live.Application.get_application().browser
      user_folders = browser.user_folders

      # Get a ref to Plustype user browser folder
      for folder in user_folders:
        # XXX TODO - This could be done more reliably
        # by importing Constants into the RemoteScript
        # and checking the "uri" property against the path
        # rather than doing it by name
        if folder.name == "Plustype Library":
          break

      ptlib = folder

      loadables = [x for x in ptlib.children if x.is_loadable]

      for loadable in loadables:

        try:

          name = loadable.name

          id = int(name.split("_")[0])

          if bp_id == id:
            return loadable

        except:
          continue

      return None

    loadable = getBrowserItemForID(bp_id)
    browser = Live.Application.get_application().browser

    if loadable is not None:
      candidate_clipslot = selectNextEmptySlotOnTemplateTrack()
      browser.load_item(loadable)
      candidate_clipslot.clip.name = newName

  def registerClips_cb(self, msg, source):
    """Treated as the main initialization function - this is called
        when requested from the Python Syringe Engine"""
    if LiveUtils.LiveVersion() < 9:
      self.set_suppress_rebuild_requests(True)
    else:
      self._set_suppress_rebuild_requests(True)

    t = time.time()

    self.initOSC()
    self.removeListeners()
    self.addListeners()

    # Establash defaults, initialization, etc

    # Register clips
    try:
      self.sendClipInfo()
    except:
      self.ptLog(str(traceback.format_exc()))

    # Register params
    self.registerParams()

    # Reset all tracks
    self.resetAllTracks()

    # Send over current tempo
    self.tempo_change()

    # Ensure global quantization is at one bar
    LiveUtils.getSong().clip_trigger_quantization = Live.Song.Quantization.q_bar

    # Ensure the Quick Cue track is cued
    LiveUtils.soloTrack(LiveUtils.getQuickCueTrack())
    LiveUtils.muteTrack(LiveUtils.getQuickCueTrack())

    # Ensure cue master track, sending master to cue output, is cued
    # XXX and unmuted?
    LiveUtils.enableCueMaster()

    # Send over current gain level of all FX tracks
    tracks = LiveUtils.getFXTracks()
    for track in tracks:
      self.oscSend("trackLevelSetting", LiveUtils.absoluteIndex(track),
                   track.mixer_device.volume.value)

    # Notify that registration is complete
    self.oscSend("clipsRegistered")
    dT = time.time() - t
    self.ptLog("Registration completed in %s seconds" % dT)

    if LiveUtils.LiveVersion() < 9:
      self.set_suppress_rebuild_requests(False)
    else:
      self._set_suppress_rebuild_requests(False)

  def playClip_cb(self, msg, source):
    track = msg[2]
    clip = msg[3]
    LiveUtils.launchClip(track, clip)

  def playClipAtTempo_cb(self, msg, source):
    track = msg[2]
    clip = msg[3]
    bpm = msg[4]
    self.clipAtTempo[LiveUtils.canonicalIndex(track)] = bpm
    LiveUtils.launchClip(track, clip)

  def stopTrack_cb(self, msg, source):
    '''Incoming track is a clip track index'''
    track = msg[2]
    respectQuantization = msg[3]

    if not respectQuantization:
      # Temporarily set quant to None
      # Try to be in this state for as little time as possible
      old_quant = LiveUtils.getSong().clip_trigger_quantization
      song = LiveUtils.getSong()
      song.clip_trigger_quantization = Live.Song.Quantization.q_no_q

      # Unquant action
      LiveUtils.stopTrack(track)

      # Restore
      song.clip_trigger_quantization = old_quant

    else:
      LiveUtils.stopTrack(track)

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

    cI = LiveUtils.canonicalIndex(track)
    aTrack = LiveUtils.getClipTrack(cI)

    # No clip playing
    if aTrack.playing_slot_index <= -1:
      self.resetTrack(LiveUtils.getFXTrack(cI))

  def selectScene_cb(self, msg, source):
    scene = msg[2]
    LiveUtils.selectScene(scene)

  def selectTrack_cb(self, msg, source):
    track = msg[2]
    LiveUtils.selectTrack(track)

  def selectClipTrack_cb(self, msg, source):
    track = msg[2]
    LiveUtils.selectTrack(LiveUtils.absoluteIndex(
        LiveUtils.getClipTrack(track)))

  def watchSlot_cb(self, msg, source):
    track = int(msg[2])
    clip = int(msg[3])
    playAfterWatch = int(msg[4])

    clipObject = LiveUtils.getClip(track, clip)

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
      LiveUtils.launchClip(track, clip)

  def setVolume_cb(self, msg, source):
    track = int(msg[2])
    vol = float(msg[3])
    LiveUtils.trackVolume(track, vol)

  def setParam_cb(self, msg, source):
    """Track: Sent as val 0-8, refers to FX track, not clip track
        ID: The label of the parameter to control
        Val: Val for param"""
    track = int(msg[2])
    id = str(msg[3])
    value = float(msg[4])

    param = self.getParameterAtTrackByID(track, id)
    self.setParameterValue(0.0, 1.0, value, param)

  def mute_cb(self, msg, source):
    track = int(msg[2])
    muteval = int(msg[3])
    trackO = LiveUtils.getFXTrack(track)
    trackO.mute = muteval

  # Note: In Live, when headphone cueing is enabled,
  # soloing is not. As a result, to be able to use soloing
  # as a performance element, we implement solo-ing through
  # muting tracks.
  # When cueing is enabled, the solo property controls
  # cue output. All solo and cue callbacks reflect this
  def exclusiveSolo_cb(self, msg, source):
    track = int(msg[2])
    soloval = int(msg[3])
    trackO = LiveUtils.getFXTrack(track)
    if soloval == False:

      # May want to check here to see if any of
      # the tracks are cueing here, where we have
      # decided that we mute tracks routing to master.
      # This is a matter of taste, let's see how it goes.
      LiveUtils.unMuteAllTracks()
    else:
      LiveUtils.exclusiveUnmuteTrack(trackO)

  def solo_cb(self, msg, source):
    track = int(msg[2])
    soloval = int(msg[3])
    trackO = LiveUtils.getFXTrack(track)
    if soloval == False:
      LiveUtils.unMuteTrack(trackO)
    else:
      LiveUtils.unMuteTrack(trackO)

  def cue_cb(self, msg, source):
    track = int(msg[2])
    cueval = int(msg[3])
    trackO = LiveUtils.getFXTrack(track)
    if cueval == False:
      LiveUtils.unSoloTrack(trackO)
    else:
      LiveUtils.soloTrack(trackO)

  def loopControlDuration_cb(self, msg, source):
    track = int(msg[2])
    scene = int(msg[3])
    duration = float(msg[4])

    clipTrack = LiveUtils.getClipTrack(track)

    clip = LiveUtils.getClip(LiveUtils.absoluteIndex(clipTrack), scene)

    if clip.looping == True:
      clip.loop_end = clip.loop_start + duration

  def loopControl_cb(self, msg, source):

    execfile("/Users/mtf/Desktop/hackshit.py", globals(), locals())

    return

    track = int(msg[2])
    scene = int(msg[3])
    duration = float(msg[4])

    clipTrack = LiveUtils.getClipTrack(track)

    clip = LiveUtils.getClip(LiveUtils.absoluteIndex(clipTrack), scene)

    #if clip.looping == True:
    #    clip.looping = False

    # We're not already looping. Check the playing
    # position
    #else:

    playpos = clip.playing_position
    clip.looping = True

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

    startPos = ((int((playpos - 1) / duration)) * duration)

    #self.ptLog("playing pos")
    #self.ptLog(playpos)
    #self.ptLog("loop start")
    #self.ptLog(startPos)

    clip.loop_start = startPos
    clip.loop_end = startPos + duration

  def loop_cb(self, msg, source):
    track = int(msg[2])
    scene = int(msg[3])
    loopval = int(msg[4])

    clipTrack = LiveUtils.getClipTrack(track)

    clip = LiveUtils.getClip(LiveUtils.absoluteIndex(clipTrack), scene)
    clip.looping = loopval

  def pokeClipParameters_cb(self, msg, source):
    track = int(msg[2])
    clip = int(msg[3])

    clipListeners = self.clipParameters.get(track, None)
    if clipListeners == None:
      return None

    for listenerTuple in clipListeners:
      obj = listenerTuple[0]
      prop = listenerTuple[1]
      if hasattr(obj, prop):
        propVal = getattr(obj, prop)

        track = LiveUtils.canonicalIndex(LiveUtils.getTrack(track))

        self.oscSend("clipParamChange", track, clip, prop, propVal)

  def pokeTrackParameters_cb(self, msg, source):
    track = int(msg[2])

    try:
      params = self.trackParameters[track]
    except IndexError:
      return None

    for param in params:
      self.oscSend("trackParamChange", track, self.makeParamStringID(param),
                   self.getNormalizedParameterValue(param))

  def watchSelectionChange_cb(self, msg, source):
    onOrOff = int(msg[2])

    songObject = LiveUtils.getSong()
    viewObject = songObject.view
    listenerTuple = (viewObject, "detail_clip", self.detail_clip)

    if onOrOff:
      #self.ptLog("watchSelectionChange: Enabled")
      self.refreshListener(*listenerTuple)
      # We've just turned on selection callback, artificially
      # induce selection change once to get things started in
      # the clip tagger
      listenerTuple[-1]()

    else:
      #self.ptLog("watchSelectionChange: Disabled")
      self.removeListenerByName(*listenerTuple)

  # Some mad hacky shit so I can run
  # code in the Ableton python environment w/o
  # reloading this plugin over and over again
  def execCode_cb(self, msg, source):
    code = str(msg[2])
    buffer = StringIO()
    sys.stdout = buffer

    exec code in globals(), self.interpreterLocals

    sys.stdout = sys.__stdout__
    self.ptLog(buffer.getvalue())

  def resetTrack_cb(self, msg, source):
    track = int(msg[2])
    self.resetTrack(LiveUtils.getFXTrack(track))

  def showDetailClip_cb(self, msg, source):
    LiveUtils.showView("Detail/Clip")

  # ##################################
  # Listeners (Run when state changes in Ableton)
  # ##################################

  def addListeners(self):
    #self.ptLog("Adding listeners")

    # Global current time
    self.refreshListener(self.song(), "current_song_time",
                         self.current_song_time_change)

    # Global tempo
    self.refreshListener(self.song(), "tempo", self.tempo_change)

    # Global gain
    self.refreshListener(self.song().master_track, "output_meter_left",
                         self.master_level_change)

    fxTracks = LiveUtils.getFXTracks()
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

    clipTracks = LiveUtils.getClipTracks()
    for track in clipTracks:

      # Observe the fired clip in all clip tracks
      cb = lambda track=track: self.fired_slot_index_change(track)
      self.refreshListener(track, "fired_slot_index", cb)

  def removeListeners(self):
    #self.ptLog("Removing listeners")
    for listenerTuple in self.listeners.keys():
      self.removeListenerByName(*listenerTuple)

  def refreshListener(self, obj, property, cbFunc):
    self.removeListenerByName(obj, property, cbFunc)
    self.addListenerByName(obj, property, cbFunc)

  def addListenerByName(self, obj, property, cbFunc):
    testStr = "obj.%s_has_listener(cbFunc)" % property
    test = eval(testStr)
    if test != 1:
      #self.ptLog("Adding by name: %s, %s, %s" % (obj, property, cbFunc.__name__))
      testStr = "obj.add_%s_listener(cbFunc)" % property
      eval(testStr)

      # Add to our list of listeners
      listenerTuple = (obj, property, cbFunc)
      self.listeners[listenerTuple] = True

  def removeListenerByName(self, obj, property, cbFunc):
    testStr = "obj.%s_has_listener(cbFunc)" % property
    test = eval(testStr)
    if test == 1:
      #self.ptLog("Removing by name: %s, %s, %s" % (obj, property, cbFunc.__name__))
      testStr = "obj.remove_%s_listener(cbFunc)" % property
      eval(testStr)

      # Remove from our list of listeners, if it's in there
      listenerTuple = (obj, property, cbFunc)
      if self.listeners.has_key(listenerTuple):
        del self.listeners[listenerTuple]
    else:
      pass
      #self.ptLog("No listener found for: %s, %s, %s" % (obj, property, cbFunc))

  # ##################################
  # Listener callbacks (Run when state changes in Ableton)
  # ##################################

  def master_level_change(self):
    level = self.song().master_track.output_meter_left
    self.oscSend("masterLevel", level)

  def tempo_change(self):
    #tempo = int(LiveUtils.getTempo())
    tempo = LiveUtils.getTempo()
    self.oscSend("tempo", tempo)

  def detail_clip(self):
    detailClip = LiveUtils.getDetailClip()

    if detailClip:
      try:
        clipname = detailClip.name
        clippath = detailClip.file_path
      except AttributeError:
        return

      selTrack, selScene = LiveUtils.getSelectedTrackAndSceneIndex()

      clipname = clipname.encode('utf-8')
      clippath = clippath.encode('utf-8')

      self.oscSend("selectionChange", clipname, clippath, selTrack, selScene)

  def playing_status_change(self, track, clip, clipObject):
    if clipObject.is_triggered == 1:
      self.oscSend("playWillTrigger", track, clip)
    else:
      if clipObject.is_playing == 1:
        self.oscSend("playTriggered", track, clip)
        playAtBPM = self.clipAtTempo.get(LiveUtils.canonicalIndex(track), None)
        if playAtBPM is not None:
          self.deferredFuncs.append({
              "func": LiveUtils.setTempo,
              "args": [playAtBPM]
          })
          self.clipAtTempo[LiveUtils.canonicalIndex(track)] = None
      else:
        self.oscSend("clipStopped", track, clip)
        self.clipAtTempo[LiveUtils.canonicalIndex(track)] = None

  def playing_position_change(self, track, clip, clipObject):
    track = LiveUtils.canonicalIndex(LiveUtils.getTrack(track))
    #end = float(clipObject.end_marker)
    #pos = clipObject.playing_position / end
    # Send the playing position in beats
    self.oscSend("playing_position", track, clip, clipObject.playing_position)

  def quick_cue_playing_position_change(self, clip, clipObject):
    self.oscSend("quick_cue_playing_position", clip,
                 clipObject.playing_position)

  def start_marker_change(self, track, clip, clipObject):
    track = LiveUtils.canonicalIndex(LiveUtils.getTrack(track))
    self.oscSend("start_marker", track, clip, clipObject.start_marker)

  def end_marker_change(self, track, clip, clipObject):
    track = LiveUtils.canonicalIndex(LiveUtils.getTrack(track))
    self.oscSend("end_marker", track, clip, clipObject.end_marker)

  def loop_start_change(self, track, clip, clipObject):
    track = LiveUtils.canonicalIndex(LiveUtils.getTrack(track))
    self.oscSend("loop_start", track, clip, clipObject.loop_start)

  def loop_end_change(self, track, clip, clipObject):
    track = LiveUtils.canonicalIndex(LiveUtils.getTrack(track))
    self.oscSend("loop_end", track, clip, clipObject.loop_end)

  def looping_change(self, track, clip, clipObject):
    """Called when a watched clip's looping status changes"""
    track = LiveUtils.canonicalIndex(LiveUtils.getTrack(track))
    self.oscSend("looping", track, clip, int(clipObject.looping))

  def output_meter_left_change(self, track):
    self.oscSend("trackLevelLeft", LiveUtils.absoluteIndex(track),
                 track.output_meter_left)

  def output_meter_right_change(self, track):
    self.oscSend("trackLevelRight", LiveUtils.absoluteIndex(track),
                 track.output_meter_right)

  def volume_change(self, track):
    self.oscSend("trackLevelSetting", LiveUtils.absoluteIndex(track),
                 track.mixer_device.volume.value)

  def fired_slot_index_change(self, track):

    # Stop will trigger
    if track.fired_slot_index == -2:
      self.oscSend("stopWillTrigger", LiveUtils.absoluteIndex(track))
      self.clipAtTempo[LiveUtils.canonicalIndex(track)] = None

    if track.fired_slot_index == -1:
      # Clip track has JUST stopped
      if track.playing_slot_index <= -1:
        fxT = LiveUtils.clipTrackToFxTrack(track)
        self.deferredFuncs.append({"func": self.resetTrack, "args": [fxT]})
        self.clipAtTempo[LiveUtils.canonicalIndex(track)] = None

  def current_song_time_change(self):
    beat = int((self.song().current_song_time % 4) + 1)

    if beat == 1:
      for defFunc in self.quantizedFuncs:
        args = defFunc.get("args", [])
        kwargs = defFunc.get("kwargs", {})
        func = defFunc.get("func", None)
        if func is not None:
          func.__call__(*args, **kwargs)
          self.ptLog("Calling deferred quantized function")
      self.quantizedFuncs = []

    if beat != self.lastBeat:
      self.oscSend("metronome", beat)
      self.lastBeat = beat

  def mute_change(self, track):
    """Called when mute changes on an FX track"""
    self.oscSend("mute", LiveUtils.absoluteIndex(track), int(track.mute))

  def solo_change(self, track):
    """Called when solo changes on an FX track"""
    self.oscSend("solo", LiveUtils.absoluteIndex(track), int(track.solo))

  # ##################################
  # Ableton methods
  # ##################################
  def update_display(self):
    """Ableton calls this every 100ms, so we use it
        to process UDP incoming events"""

    # Keep trying to get the song if we haven't been
    # able to yet.  When we get it, initialize
    if self.songReady is False:
      try:
        doc = self.song()
      except:
        return

      self.songReady = True
      self.initOSC()

    if self.oscEndpoint:
      try:
        self.oscEndpoint.processIncomingUDP()
      except:
        self.initOSC()
        self.ptLog('Error processing incoming UDP packets:' +
                   str(traceback.format_exc()))

    for defFunc in self.deferredFuncs:
      args = defFunc.get("args", [])
      kwargs = defFunc.get("kwargs", {})
      func = defFunc.get("func", None)
      if func is not None:
        func.__call__(*args, **kwargs)
    self.deferredFuncs = []

  def disconnect(self):
    self.ptLog("Disconnect in Plustype RemoteScript")
    self.removeListeners()
    self.oscEndpoint.shutdown()
    ControlSurface.disconnect(self)

  # ##################################

  def ptShow(self, msg):
    """Shows a message in Live's status bar"""
    sMsg = str(msg)
    self.show_message(sMsg)

  def ptLog(self, msg):
    """Writes to the Log.txt in the Live Application bundle"""
    sMsg = str(msg)
    self.oscSend("log", sMsg)
    self.log_message(sMsg)
