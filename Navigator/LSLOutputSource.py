from __future__ import annotations

import asyncio

import attrs
import logging
import numpy as np
import pylsl as lsl
import pytransform3d.transformations as ptt

from RTNaBS.Navigator.Model.Addons import AddonExtra
from RTNaBS.Navigator.TargetingCoordinator import TargetingCoordinator
from RTNaBS.util.Asyncio import asyncTryAndLogExceptionOnError
from RTNaBS.util.Transforms import concatenateTransforms, applyTransform, invertTransform

from NaviNIBS_LSL_Output.Navigator.Model.LSLOutputConfiguration import LSLOutputConfiguration

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

posAndQuatSuffixes = ['Tx', 'Ty', 'Tz', 'Qw', 'Qx', 'Qy', 'Qz']
xyzSuffixes = ['Tx', 'Ty', 'Tz']


@attrs.define
class LSLOutputSource(AddonExtra):

    _config: LSLOutputConfiguration = attrs.field(init=False)
    _targetingCoordinator: TargetingCoordinator = attrs.field(init=False)
    _floatOutputStream: lsl.StreamOutlet | None = attrs.field(init=False, default=None)
    _strOutputStream: lsl.StreamOutlet | None = attrs.field(init=False, default=None)

    _floatChannelMapping: list[str] = attrs.field(init=False, factory=list)
    _strChannelMapping: list[str] = attrs.field(init=False, factory=list)

    _latestPositionsChanged: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)
    _targetChanged: asyncio.Event = attrs.field(init=False, factory=asyncio.Event)

    def __attrs_post_init__(self):
        super().__attrs_post_init__()

        self._config = self._session.addons['NaviNIBS_LSL_Output'].LSLOutput
        assert isinstance(self._config, LSLOutputConfiguration)

        self._targetingCoordinator = TargetingCoordinator.getSingleton(session=self._session)

        self._targetingCoordinator.sigCurrentTargetChanged.connect(self._onCurrentTargetChanged)
        self._targetingCoordinator.positionsClient.sigLatestPositionsChanged.connect(self._onLatestPositionsChanged)

        asyncio.create_task(asyncTryAndLogExceptionOnError(self._loop_stream))

    def _initializeFloatStream(self):
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

    def _initializeStrStream(self):
        if self._strOutputStream is not None:
            raise NotImplementedError  # TODO: implement for re-initializing

        assert len(self._strChannelMapping) == 0

        if self._config.doStreamCurrentTarget:
            self._strChannelMapping.append('currentTargetName')

        if len(self._strChannelMapping) == 0:
            logger.info('No string channels selected for streaming. Not initializing string stream.')
            self._strOutputStream = None
            return

        info = lsl.StreamInfo(
            name=self._config.strOutputStreamName,
            type='NaviNIBS_String',
            channel_count=len(self._strChannelMapping),
            nominal_srate=lsl.IRREGULAR_RATE,
            channel_format=lsl.cf_string,
            source_id=self._config.strOutputStreamName,
        )

        channels = info.desc().append_child('channels')
        for key in self._strChannelMapping:
            channels.append_child('channel').append_child_value('label', key)

        logger.info(f'Initializing LSL stream {info.name()}')
        logger.debug(f'LSl stream info: {info.as_xml()}')

        self._strOutputStream = lsl.StreamOutlet(info)

    def _onLatestPositionsChanged(self):
        if any([self._config.doStreamActiveCoilPose,
                self._config.doStreamPointerPose,
                self._config.doStreamTrackerPose]):
            self._latestPositionsChanged.set()

    def _onCurrentTargetChanged(self):
        if self._config.doStreamCurrentTarget:
            self._targetChanged.set()

    async def _loop_stream(self):

        self._initializeFloatStream()
        self._initializeStrStream()

        timeOfLastUpdate = lsl.local_clock()

        while True:
            if self._config.streamFloatsAsIntermittent:
                await asyncio.wait([
                    self._latestPositionsChanged.wait(),
                    self._targetChanged.wait(),
                ], return_when=asyncio.FIRST_COMPLETED)

                await asyncio.sleep(self._config.updateRate)  # rate limit
            else:
                waitTime = 1/self._config.updateRate - (lsl.local_clock() - timeOfLastUpdate)
                if waitTime > 0:
                    await asyncio.sleep(waitTime)

            timeOfLastUpdate = lsl.local_clock()

            logger.debug('Preparing sample to send')

            doSendStrSample = False

            if self._targetChanged.is_set():
                self._targetChanged.clear()
                if self._config.doStreamCurrentTarget:
                    # if not streaming other str variables, only send a str sample when target changes
                    doSendStrSample = True

            self._latestPositionsChanged.clear()

            sample = np.full(len(self._floatChannelMapping), np.nan)

            def encodeSamplePart_transf(transf: np.ndarray | None,
                                        prefix: str,
                                        sample: np.ndarray):
                #logger.debug(f'encodeSamplePart_transf: {prefix}')
                if transf is None:
                    pq = np.full(7, np.nan)
                else:
                    pq = ptt.pq_from_transform(transf)

                for i, suffix in enumerate(posAndQuatSuffixes):
                    key = prefix + suffix
                    sample[self._floatChannelMapping.index(key)] = pq[i]

            def encodeSamplePart_coord(coord: np.ndarray | None,
                                       prefix: str,
                                       sample: np.ndarray):
                #logger.debug(f'encodeSamplePart_coord: {prefix}')
                if coord is None:
                    coord = np.full(3, np.nan)
                for i, suffix in enumerate(xyzSuffixes):
                    key = prefix + suffix
                    sample[self._floatChannelMapping.index(key)] = coord[i]

            def getToolToOutputSpaceTransf(toolKey: str) -> np.ndarray | None:
                #logger.debug(f'getToolToOutputSpaceTransf')
                match self._config.streamPosesInSpace:
                    case 'MRI':
                        toolTrackerToCameraTransf = self._targetingCoordinator.positionsClient.getLatestTransf(
                            self._session.tools[toolKey].key, None)

                        subjectTrackerToCameraTransf = self._targetingCoordinator.positionsClient.getLatestTransf(
                            self._session.tools.subjectTracker.key, None)
                        toolToTrackerTransf = self._session.tools[toolKey].toolToTrackerTransf

                        subjectTrackerToMRITransf = self._session.subjectRegistration.trackerToMRITransf

                        if toolTrackerToCameraTransf is None or \
                                subjectTrackerToCameraTransf is None or \
                                toolToTrackerTransf is None or \
                                subjectTrackerToMRITransf is None:
                            # cannot compute valid pose
                            return None

                        toolToMRITransform = concatenateTransforms([
                            toolToTrackerTransf,
                            toolTrackerToCameraTransf,
                            invertTransform(subjectTrackerToCameraTransf),
                            subjectTrackerToMRITransf
                        ])

                        return toolToMRITransform
                    case _:
                        raise NotImplementedError

            if self._config.doStreamActiveCoilPose:
                try:
                    coilKey = self._targetingCoordinator.activeCoilKey
                except KeyError:
                    coilKey = None
                if coilKey is None:
                    transf = None
                else:
                    transf = getToolToOutputSpaceTransf(coilKey)
                encodeSamplePart_transf(transf, 'activeCoilPose', sample)

                # also include angle from midline
                #logger.debug('get angle from midline')
                key = 'activeCoilAngleFromMidline'
                sample[self._floatChannelMapping.index(key)] = self._targetingCoordinator.currentPoseMetrics.getAngleFromMidline()

            if self._config.doStreamPointerPose:
                pointer = self._session.tools.pointer
                if pointer is None:
                    transf = None
                else:
                    transf = getToolToOutputSpaceTransf(pointer.key)
                encodeSamplePart_transf(transf, 'pointerPose', sample)

            if self._config.doStreamTrackerPose:
                tracker = self._session.tools.subjectTracker
                if tracker is None:
                    transf = None
                else:
                    transf = getToolToOutputSpaceTransf(tracker.key)
                encodeSamplePart_transf(transf, 'trackerPose', sample)

            if self._config.doStreamCurrentTarget:
                target = self._targetingCoordinator.currentTarget
                if target is None:
                    pass  # sample should already have NaNs for all relevant fields
                else:
                    transf = target.coilToMRITransf
                    match self._config.streamPosesInSpace:
                        case 'MRI':
                            pass  # no further modification needed
                        case _:
                            raise NotImplementedError
                    encodeSamplePart_transf(transf, 'currentTargetPose', sample)

                    # note: coordinates are in MRI space, regardless of config.streamPosesInSpace setting
                    encodeSamplePart_coord(target.targetCoord, 'currentTargetTargetCoord', sample)
                    encodeSamplePart_coord(target.entryCoord, 'currentTargetEntryCoord', sample)

                    sample[self._floatChannelMapping.index('currentTargetAngle')] = target.angle
                    sample[self._floatChannelMapping.index('currentTargetDepthOffset')] = target.depthOffset

            if len(sample) > 0:
                logger.debug('Pushing float sample')
                self._floatOutputStream.push_sample(sample, lsl.local_clock())

            if doSendStrSample:
                strSample = [''] * len(self._strChannelMapping)
                if self._config.doStreamCurrentTarget:
                    target = self._targetingCoordinator.currentTarget
                    if target is None:
                        targetName = ''
                    else:
                        targetName = target.key
                    strSample[self._strChannelMapping.index('currentTargetName')] = targetName

                logger.debug('Pushing str sample')
                self._strOutputStream.push_sample(strSample)

            logger.debug('End of loop')


