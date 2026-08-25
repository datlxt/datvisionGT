from __future__ import annotations

from dataclasses import dataclass, field

from app.vision.plate.domain import FrameObservation, VehicleTrack, bbox_iou


@dataclass(slots=True)
class GreedyVehicleTracker:
    """Deterministic CPU baseline for a fixed, single-direction toll lane."""

    iou_threshold: float = 0.2
    max_gap_ms: int = 1250
    # A single toll-lane pass — even a vehicle briefly idling at the gate — lasts tens of seconds at
    # most (the real plated passes here run 3–21s). A NO-PLATE track that stays alive far longer is
    # the tracker latching onto continuous gate/queue activity at a fixed lane spot; it SWALLOWS
    # distinct no-plate vehicles that cross that spot (e.g. a bicycle) into one giant blob which is
    # then dropped wholesale as garbage — so the crossing vehicle never becomes its own event.
    # Force-closing such a track lets the next observation seed a FRESH track, so a no-plate vehicle
    # in an otherwise-empty stretch surfaces as its own "Xe không biển" case. Only applied while the
    # track has read NO plate: a track that already carries a plate is a positively-identified
    # vehicle and is NEVER split (protects the working recognized passes); and any over-split of a
    # genuinely long single pass is re-merged downstream by consolidate_vehicle_events.
    max_no_plate_lifetime_ms: int = 30_000
    _next_id: int = 1
    _active: list[VehicleTrack] = field(default_factory=list)

    def update(self, observations: list[FrameObservation], timestamp_ms: int) -> list[VehicleTrack]:
        closed = [
            track
            for track in self._active
            if timestamp_ms - track.last.timestamp_ms > self.max_gap_ms
            or (
                timestamp_ms - track.observations[0].timestamp_ms > self.max_no_plate_lifetime_ms
                and not any(item.plate_bbox is not None for item in track.observations)
            )
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
