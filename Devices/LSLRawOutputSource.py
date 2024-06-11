from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pylsl as lsl
import pytransform3d.transformations as ptt

from NaviNIBS.Devices.ToolPositionsClient import ToolPositionsClient, TimestampedToolPosition
from NaviNIBS.Navigator.Model.Addons import AddonExtra
from NaviNIBS.Navigator.TargetingCoordinator import TargetingCoordinator
from NaviNIBS.util.Asyncio import asyncTryAndLogExceptionOnError, asyncWait
from NaviNIBS.util.Transforms import concatenateTransforms, applyTransform, invertTransform

from NaviNIBS_LSL_Output.Navigator.Model.LSLOutputConfiguration import LSLOutputConfiguration

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

posAndQuatSuffixes = ['Tx', 'Ty', 'Tz', 'Qw', 'Qx', 'Qy', 'Qz']
xyzSuffixes = ['Tx', 'Ty', 'Tz']


@attrs.define
class LSLRawOutputSource:
    _positionsClient: ToolPositionsClient = attrs.field(init=False)
    _pendingUpdate: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        self._positionsClient = ToolPositionsClient()
        self._positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._finishInitialization_async))

    async def _finishInitialization_async(self):
        connectedEvt = asyncio.Event()
        self._positionsClient.sigIsConnectedChanged.connect(lambda: connectedEvt.set() if self._positionsClient.isConnected else connectedEvt.clear())
        await connectedEvt.wait()

        await self._loop_stream()

    async def _loop_stream(self):
        await self._pendingUpdate.wait()
        self._pendingUpdate.clear()



        raise NotImplementedError

    def _onLatestPositionsChanged(self):
        self._pendingUpdate.set()

    def _initializeFloatStream(self):

        raise NotImplementedError  # TODO


        if self._floatOutputStream is not None:
            raise NotImplementedError  # TODO: implement for re-initializing

        assert len(self._floatChannelMapping) == 0
        floatChannelUnits = []

        if self._config.doStreamActiveCoilPose:
            for suffix in posAndQuatSuffixes:
                self._floatChannelMapping.append('activeCoilPose' + suffix)
                if suffix.startswith('T'):
                    floatChannelUnits.append('mm')
                else:
                    floatChannelUnits.append('normalized')

            # also stream computed angle from midline
            self._floatChannelMapping.append('activeCoilAngleFromMidline')
            floatChannelUnits.append('degrees')

        if self._config.doStreamPointerPose:
            for suffix in posAndQuatSuffixes:
                self._floatChannelMapping.append('pointerPose' + suffix)
                if suffix.startswith('T'):
                    floatChannelUnits.append('mm')
                else:
                    floatChannelUnits.append('normalized')

        if self._config.doStreamTrackerPose:
            for suffix in posAndQuatSuffixes:
                self._floatChannelMapping.append('trackerPose' + suffix)
                if suffix.startswith('T'):
                    floatChannelUnits.append('mm')
                else:
                    floatChannelUnits.append('normalized')

        if self._config.doStreamCurrentTarget:
            for suffix in posAndQuatSuffixes:
                self._floatChannelMapping.append('currentTargetPose' + suffix)
                if suffix.startswith('T'):
                    floatChannelUnits.append('mm')
                else:
                    floatChannelUnits.append('normalized')

            for coord in ['TargetCoord', 'EntryCoord']:
                for suffix in xyzSuffixes:
                    self._floatChannelMapping.append('currentTarget' + coord + suffix)
                    floatChannelUnits.append('mm')

            self._floatChannelMapping.append('currentTargetAngle')
            floatChannelUnits.append('degrees')
            self._floatChannelMapping.append('currentTargetDepthOffset')
            floatChannelUnits.append('mm')

        if len(self._floatChannelMapping) == 0:
            logger.info('No float channels selected for streaming. Not initializing float stream.')
            self._floatOutputStream = None
            return

        info = lsl.StreamInfo(
            name=self._config.floatOutputStreamName,
            type='NaviNIBS_Float',
            channel_count=len(self._floatChannelMapping),
            nominal_srate=lsl.IRREGULAR_RATE if self._config.streamFloatsAsIntermittent else self._config.updateRate,
            channel_format=lsl.cf_double64,  # TODO: consider dropping down to cf_float32
            source_id=self._config.floatOutputStreamName,
        )

        channels = info.desc().append_child('channels')
        for i, key in enumerate(self._floatChannelMapping):
            channel = channels.append_child('channel')
            channel.append_child_value('label', key)
            channel.append_child_value('unit', floatChannelUnits[i])

        logger.info(f'Initializing LSL stream {info.name()}')
        logger.debug(f'LSl stream info: {info.as_xml()}')

        self._floatOutputStream = lsl.StreamOutlet(info)