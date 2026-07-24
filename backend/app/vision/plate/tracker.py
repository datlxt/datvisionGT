from __future__ import annotations

from dataclasses import dataclass, field

from app.vision.plate.domain import FrameObservation, VehicleTrack, bbox_iou


@dataclass(slots=True)
class GreedyVehicleTracker:
    """Deterministic CPU baseline for a fixed, single-direction toll lane."""

    iou_threshold: float = 0.2
    max_gap_ms: int = 1250
    _next_id: int = 1
    _active: list[VehicleTrack] = field(default_factory=list)

    def update(self, observations: list[FrameObservation], timestamp_ms: int) -> list[VehicleTrack]:
        closed = [
            track
            for track in self._active
            if timestamp_ms - track.last.timestamp_ms > self.max_gap_ms
        ]
        self._active = [track for track in self._active if track not in closed]
        unmatched = list(observations)

        pairs: list[tuple[float, VehicleTrack, FrameObservation]] = []
        for track in self._active:
            for observation in unmatched:
                pairs.append(
                    (
                        bbox_iou(track.last.vehicle_bbox, observation.vehicle_bbox),
                        track,
                        observation,
                    )
                )
        used_tracks: set[str] = set()
        used_observations: set[int] = set()
        for score, track, observation in sorted(pairs, key=lambda item: item[0], reverse=True):
            marker = id(observation)
            if (
                score < self.iou_threshold
                or track.track_code in used_tracks
                or marker in used_observations
            ):
                continue
            track.observations.append(observation)
            used_tracks.add(track.track_code)
            used_observations.add(marker)

        for observation in unmatched:
            if id(observation) in used_observations:
                continue
            track = VehicleTrack(
                track_code=f"VEHICLE_{self._next_id:06d}", observations=[observation]
            )
            self._next_id += 1
            self._active.append(track)
        return closed

    def flush(self) -> list[VehicleTrack]:
        remaining = self._active
        self._active = []
        return remaining
