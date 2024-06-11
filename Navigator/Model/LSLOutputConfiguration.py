from __future__ import annotations
import attrs
import typing as tp

from NaviNIBS.Navigator.Model.Addons import AddonSessionConfig
from NaviNIBS.Navigator.Model.Session import Session
from NaviNIBS.util.attrs import attrsAsDict


@attrs.define
class LSLOutputConfiguration(AddonSessionConfig):
    _floatOutputStreamName: str = 'NaviNIBS_Float'
    _strOutputStreamName: str = 'NaviNIBS_Str'

    _streamPosesInSpace: str = 'MRI'
    _doStreamActiveCoilPose: bool = True
    _doStreamPointerPose: bool = False
    _doStreamTrackerPose: bool = False
    _doStreamCurrentTarget: bool = True

    _streamFloatsAsIntermittent: bool = False

    _updateRate: float = 2  # in Hz

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

    @property
    def floatOutputStreamName(self):
        return self._floatOutputStreamName

    @property
    def strOutputStreamName(self):
        return self._strOutputStreamName

    @property
    def streamPosesInSpace(self):
        return self._streamPosesInSpace

    @property
    def doStreamActiveCoilPose(self):
        return self._doStreamActiveCoilPose

    @property
    def doStreamPointerPose(self):
        return self._doStreamPointerPose

    @property
    def doStreamTrackerPose(self):
        return self._doStreamTrackerPose

    @property
    def doStreamCurrentTarget(self):
        return self._doStreamCurrentTarget

    @property
    def streamFloatsAsIntermittent(self):
        return self._streamFloatsAsIntermittent

    @property
    def updateRate(self):
        return self._updateRate

    def asDict(self) -> tp.Dict[str, tp.Any]:
        return attrsAsDict(self, exclude={'session'})