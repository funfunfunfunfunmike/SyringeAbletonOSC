import Live


def LiveVersion():
  return Live.Application.get_application().get_major_version()


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


def getSong():
  """Gets a the current Song instance"""
  return Live.Application.get_application().get_document()


def continuePlaying():
  """Continues Playing"""
  getSong().continue_playing()


def playSelection():
  """Plays the current selection"""
  getSong().play_selection()


def jumpBy(time):
  """Jumps the playhead relative to it's current position by time.  Stops playback."""
  getSong().jump_by(time)


def scrubBy(time):
  """Jumps the playhead relative to it's current position by time.  Does not stop playback"""
  getSong().scrub_by(time)


def play():
  """Starts Ableton Playing"""
  getSong().start_playing()


def stopClips():
  """Stops all currently playing clips"""
  getSong().stop_all_clips()


def stop():
  """Stops Ableton"""
  getSong().stop_playing()


def currentTime(time=None):
  """Sets/Returns the current song time"""
  song = getSong()
  if time is not None:
    song.current_song_time = time
  return getSong().current_song_time


def getScenes():
  """Returns a list of scenes"""
  return getSong().scenes


def getScene(num):
  """Returns scene number (num) (starting at 0)"""
  return getSong().scenes[num]


def launchScene(scene):
  """Launches scene number (scene)"""
  getScene(scene).fire()


def isTrackFXTrack(track):
  return track.name.endswith("FX")


def isTrackClipTrack(track):
  return track.name.endswith("CLIPS")


def enableCueMaster():
  tracks = getCueMasterTracks()
  for track in tracks:
    soloTrack(track)


def disableCueMaster():
  tracks = getCueMasterTracks()
  for track in tracks:
    unSoloTrack(track)


# Tracks for sending master to cue output
# These are all the CUE'd FX tracks, and instruments
def getCueMasterTracks():
  cueTracks = getFXTracks()
  cueTracks.extend([getTrackByName("INST")])
  cueTracks.extend([getTrackByName("TWISTER_STEP_SEQ")])
  #cueTracks.extend([getTrackByName("VST_VOCAL")])
  #cueTracks.extend([getTrackByName("TALKOVER")])
  return cueTracks


def getQuickCueTrack():
  return getTrackByName("QUICK_CUE")
  # XXX wtf mate, you've got a getTrackByName
  #t = [x for x in getTracks() if x.name == "QUICK_CUE"]
  #if len(t) > 0:
  #    return t[0]


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


def getTrackByName(search_name):
  """Returns track with name search_name, or None if not found"""
  for t in getTracks():
    if t.name == search_name:
      return t
  return None


def getClipByName(track, clipName):
  if track is not None and clipName is not None:
    for clip_slot in track.clip_slots:
      if clip_slot.clip is not None:
        if clip_slot.clip.name == clipName:
          return clip_slot.clip
    return None


def getTrack(num):
  """Returns track number (num) (starting at 0)"""
  return getSong().visible_tracks[num]


def stopTrack(trackNumber):
  """Stops all clips in track number (trackNumber)"""
  track = getTrack(trackNumber)
  if track is not None:
    track.stop_all_clips()

  #for clipSlot in track.clip_slots:
  # clipSlot.stop()


def getTempo():
  """Returns the current song tempo"""
  return getSong().tempo


def setTempo(tempo):
  getSong().tempo = tempo


def jumpToNextCue():
  getSong().jump_to_next_cue()


def jumpToPrevCue():
  getSong().jump_to_prev_cue()


def armTrack(num):
  """Arms track number (num)"""
  getTrack(num).arm = 1


def disarmTrack(num):
  """Disarms track number (num)"""
  getTrack(num).arm = 0


def toggleArmTrack(num):
  """Toggles the armed state of track number (num)"""
  armed = getTrack(num).arm
  if armed:
    getTrack(num).arm = 0
  else:
    getTrack(num).arm = 1


def exclusiveUnmuteTrack(track):
  """Given a track, makes sure it is the only one
    NOT muted."""
  for ct in getTracks():
    if ct == track:
      ct.mute = 0
    else:
      ct.mute = 1


def unMuteAllTracks():
  """Un-mutes all tracks"""
  for ct in getTracks():
    ct.mute = 0


def muteTrack(track):
  """Mutes track"""
  for ct in getTracks():
    if ct == track:
      ct.mute = 1


def unMuteTrack(track):
  """Unmutes track"""
  for ct in getTracks():
    if ct == track:
      ct.mute = 0


def toggleMuteTrack(track):
  """Toggles the muted state of track"""
  muted = track.mute
  if muted:
    track.mute = 0
  else:
    track.mute = 1


def exclusiveSoloTrack(track):
  """Given a track, makes sure it is the only one
    soloed. Setting solo with the API bypasses the
    exclusive solo logic, which is why we do this
    manually."""

  for ct in getTracks():
    if ct == track:
      ct.solo = 1
    else:
      ct.solo = 0


def soloTrack(track):
  """Solo track"""
  for ct in getTracks():
    if ct == track:
      ct.solo = 1


def unSoloAllTracks():
  """Un-solos all tracks"""
  for ct in getTracks():
    ct.solo = 0


def unSoloTrack(track):
  """Un-solos track"""
  for ct in getTracks():
    if ct == track:
      ct.solo = 0


def toggleSoloTrack(track):
  """Toggles the soloed state of track"""
  soloed = track.solo
  if soloed:
    track.solo = 0
  else:
    track.solo = 1


def trackVolume(track, volume=None):
  """Gets/Changes the volume of track (track)

    If (volume) is specified, changes the volume of track number
    (track) to (volume), a value between 0.0 and 1.0.
    """
  if volume != None:
    getTrack(track).mixer_device.volume.value = volume
  return getTrack(track).mixer_device.volume.value


def trackPan(track, pan=None):
  """Gets/Changes the panning of track number (track)

    If (pan) is specified, changes the panning to (pan).
    (pan) should be a value between -1.0 to 1.0
    """
  if pan != None:
    getTrack(track).mixer_device.panning.value = pan
  return getTrack(track).mixer_device.panning.value


def trackSend(track, send=None, level=None):
  """Gets/Changes the level of send number (send) on track (track).

    If (level) is specified, the level of the send is set to (level),
    a value between 0.0 and 1.0
    """
  if send == None:
    return getTrack(track).mixer_device.sends
  if level != None:
    getTrack(track).mixer_device.sends[send].value = level
  return getTrack(track).mixer_device.sends[send].value


def trackName(track, name=None):
  """Gets/Changes the name of track (track).

    If (name) is specified, the track name is changed
    """
  if name != None:
    getTrack(track).name = name
  return str(getTrack(track).name)


def getClipSlots():
  """Gets a 2D list of all the clip slots in the song"""
  tracks = getTracks()
  clipSlots = []
  for track in tracks:
    clipSlots.append(track.clip_slots)
  return clipSlots


def getClips():
  """Gets a 2D list of all the clip in the song.

    If there is no clip in a clip slot, None is returned

    """
  tracks = getTracks()
  clips = []
  for track in getClipSlots():
    trackClips = []
    for clipSlot in track:
      trackClips.append(clipSlot.clip)
    clips.append(trackClips)
  return clips


def launchClipAtTempo(track, clip, bpm):
  """Launches clip numer (clip) in track number (track)
    at bpm (bpm)"""
  setTempo(bpm)
  launchClip(track, clip)


def launchClip(track, clip):
  """Launches clip number (clip) in track number (track)"""
  getClip(track, clip).fire()


def stopClip(track, clip):
  """Stops clip number (clip) in track (track)"""
  getClip(track, clip).stop()


def getClip(track, clip):
  """Returns clip number (clip) in track (track)"""
  return getSong().visible_tracks[track].clip_slots[clip].clip


def setDetailClipName(name):
  getSong().view.detail_clip.name = str(name)


def getSelectedTrackAndSceneIndex():
  track = getSong().view.selected_track
  trackIndex = absoluteIndex(track)

  detailClip = getDetailClip()

  for clipIndex, candidate in enumerate(track.clip_slots):
    if candidate.clip == detailClip:
      return trackIndex, clipIndex

  return None, None


def getDetailClip():
  try:
    clip = getSong().view.detail_clip
  except AttributeError:
    return None
  return clip


def detailClipName():
  try:
    name = getSong().view.detail_clip.name
  except AttributeError:
    return None
  return str(name)


def clipName(track, clip, name=None):
  """Gets/changes the name of clip number (clip) in track (track)

    In (name) is specified, the name of the clip is changed

    """
  if name != None:
    getClip(track, clip).name = name
  return str(getClip(track, clip).name)


def clipPitch(track, clip, coarse=None, fine=None):
  """Gets/changes the coarse and fine pitch shift of clip (clip) in track (track).

    If (coarse) or (fine) are specified, changes the clip's pitch.
    """
  clip = getClip(track, clip)
  if coarse != None:
    clip.pitch_coarse = coarse
  if fine != None:
    clip.pitch_fine = fine
  return (clip.pitch_coarse, clip.pitch_fine)


def selectTrack(track):
  track = int(track)
  getSong().view.selected_track = getTrack(track)


def selectScene(scene):
  scene = int(scene)
  getSong().view.selected_scene = getScene(scene)


def setFollow(follow):
  follow = int(follow)
  getSong().view.follow_song = follow


def showView(view):
  Live.Application.get_application().view.show_view(view)
