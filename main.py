#!/usr/bin/env python3
"""Forecast release-day Twitch counts from checkpoint-aligned launches."""

from dataclasses import dataclass
from datetime import datetime
import json
import math
import os
from pathlib import Path
from statistics import median


LEVELS = (0.05, 0.25, 0.50, 0.75, 0.95)
HOURS = range(1, 24)  # Zero-based UTC slots. Slot 0 is already observed.
LOG_LIMIT = math.log(1_000_000_000)


def clock(value):
    return datetime.fromisoformat(value)


def log_count(value):
    return math.log(max(1, value))


def hourly_logs(rows, start):
    return {
        int((clock(row['from']) - start).total_seconds() // 3600): log_count(row['viewers'])
        for row in rows if row.get('viewers') is not None
    }


def release_type(unlock, platforms=''):
    """Use schedule language only. Provider notes can mention post-launch peaks."""
    schedule = unlock.lower().split('source:')[0].split('tier:')[0].split('research note:')[0]
    if 'early access' in schedule or 'early-access' in schedule:
        if 'no early access' not in schedule:
            return 'early'
    if 'unverified' in schedule or 'expected' in schedule:
        return 'unknown'
    if 'midnight local' in schedule or 'rolling' in schedule:
        if 'pc' in platforms.lower() or 'east coast midnight' in schedule or 'eastern' in schedule:
            return 'mixed'
        return 'rolling'
    if 'simultaneous' in schedule or 'global unlock' in schedule:
        return 'global'
    return 'unknown'


@dataclass(frozen=True)
class Checkpoint:
    scale: float
    trend: tuple
    schedule: str


@dataclass(frozen=True)
class Launch:
    checkpoint: Checkpoint
    growth: dict


def checkpoint(logs, anchor, schedule):
    trend = []
    for lower, upper in ((-2, -1), (-7, -3), (-23, -8)):
        counts = [logs[h] for h in range(lower, upper + 1) if h in logs]
        required = min(3, (upper - lower + 2) // 2)
        if len(counts) >= required:
            trend.append(max(-3.0, min(6.0, anchor - median(counts))))
        else:
            trend.append(None)
    return Checkpoint(anchor, tuple(trend), schedule)


def read_launches(case):
    target = case['game']['name'].casefold()
    launches = []
    for row in case['comparables']:
        if row['name'].casefold() == target or 'tier: target-history' in row['unlock'].lower():
            continue
        logs = hourly_logs(row['hourly'], clock(row['day_start']))
        if 0 not in logs:
            continue
        # Keep gaps as gaps. Each hour is estimated from the titles available there.
        anchor = logs[0]
        growth = {h: logs[h] - anchor for h in HOURS if h in logs}
        if growth:
            launches.append(Launch(checkpoint(logs, anchor, release_type(row['unlock'], row['platforms'])), growth))
    return launches


SCHEDULE_WEIGHTS = {
    'rolling': {'rolling': 1.0, 'mixed': 0.70, 'unknown': 0.35, 'global': 0.18, 'early': 0.12},
    'mixed': {'rolling': 0.75, 'mixed': 1.0, 'unknown': 0.45, 'global': 0.40, 'early': 0.18},
    'global': {'rolling': 0.30, 'mixed': 0.60, 'unknown': 0.65, 'global': 1.0, 'early': 0.42},
    'early': {'rolling': 0.25, 'mixed': 0.40, 'unknown': 0.65, 'global': 0.50, 'early': 1.0},
    'unknown': {'rolling': 0.75, 'mixed': 0.85, 'unknown': 1.0, 'global': 0.85, 'early': 0.65},
}


def similarity(target, donor, scale_ceiling):
    # Clamp extrapolated scale for matching so a record-sized launch still has peers.
    gap = abs(min(target.scale, scale_ceiling) - donor.scale) / 2.1
    distance = min(2.5, gap) ** 2
    for recent, past, width in zip(target.trend, donor.trend, (0.9, 1.7, 2.8)):
        if recent is not None and past is not None:
            distance += min(2.2, abs(recent - past) / width) ** 2 / 3
    return SCHEDULE_WEIGHTS[target.schedule][donor.schedule] * math.exp(-0.5 * distance)


def mixture_cdf(points, bandwidth):
    total = 2 * sum(weight for _, weight in points)
    scaled = [(value, weight / total) for value, weight in points]
    divisor = math.sqrt(2) * bandwidth

    def cdf(value):
        return sum(weight * math.erfc((center - value) / divisor) for center, weight in scaled)

    return cdf


def mixture_quantiles(points, bandwidth, levels):
    """Invert a weighted Gaussian kernel CDF in log-viewer space."""
    cdf = mixture_cdf(points, bandwidth)
    lo = min(value for value, _ in points) - 6 * bandwidth
    hi = max(value for value, _ in points) + 6 * bandwidth
    quantiles = []
    for tau in levels:
        lower, upper = lo, hi
        for _ in range(24):
            middle = (lower + upper) / 2
            if cdf(middle) < tau:
                lower = middle
            else:
                upper = middle
        quantiles.append((lower + upper) / 2)
    return quantiles


def donor_weights(target, launches):
    scales = sorted(launch.checkpoint.scale for launch in launches)
    ceiling = scales[max(0, int(len(scales) * 0.9) - 1)] if scales else target.scale
    return [similarity(target, launch.checkpoint, ceiling) for launch in launches]


def distributions(target, launches, weights):
    # Add uncertainty beyond the observed scale range without moving the median
    # toward the smaller historical launches in absolute viewer units.
    largest = max((launch.checkpoint.scale for launch in launches), default=target.scale)
    novelty = min(2.0, max(0.0, target.scale - largest))
    result = {}
    for h in HOURS:
        bandwidth = 0.12 + (0.025 + 0.035 * novelty) * math.sqrt(h)
        points = []
        for launch, weight in zip(launches, weights):
            if h in launch.growth:
                curve = launch.growth
                # A modest local average attenuates single-hour streamer spikes.
                center = curve[h]
                change = (curve.get(h - 1, 0.0 if h == 1 else center) + curve.get(h + 1, center)) / 2
                points.append((0.8 * center + 0.2 * change, weight))
        if not points:
            points = [(0.0, 1.0)]
            bandwidth = 0.15 * math.sqrt(h)
        result[h] = points, bandwidth
    return result


def calibrated_levels(launches, weights):
    """Pool cross-fitted probability integral transforms near each UTC hour."""
    pits = {h: [] for h in HOURS}
    if len(launches) < 6:
        return {h: LEVELS for h in HOURS}
    for index, launch in enumerate(launches):
        peers = launches[:index] + launches[index + 1:]
        held = launch.checkpoint
        by_hour = distributions(held, peers, donor_weights(held, peers))
        for h, truth in launch.growth.items():
            pits[h].append((mixture_cdf(*by_hour[h])(truth), weights[index]))

    levels = {}
    for h in HOURS:
        local = sorted(
            (probability, weight / (1 + abs(h - slot)))
            for slot in range(max(1, h - 2), min(23, h + 2) + 1)
            for probability, weight in pits[slot]
        )
        total = sum(weight for _, weight in local)
        if not total:
            levels[h] = LEVELS
            continue
        # Blend the empirical calibration with the identity. Neighboring hours
        # are correlated, and the closest schedule group contains few titles.
        knots = [(0.0, 0.0)]
        cumulative = 0.0
        for probability, weight in local:
            rank = (cumulative + weight / 2) / total
            knots.append((0.5 * rank + 0.5 * probability, probability))
            cumulative += weight
        knots.append((1.0, 1.0))
        mapped = []
        for tau in LEVELS:
            for (a, low), (b, high) in zip(knots, knots[1:]):
                if b >= tau:
                    mapped.append(low + (high - low) * (tau - a) / (b - a))
                    break
        levels[h] = mapped
    return levels


def predict_growth(target, launches):
    weights = donor_weights(target, launches)
    levels = calibrated_levels(launches, weights)
    return {
        h: mixture_quantiles(points, bandwidth, levels[h])
        for h, (points, bandwidth) in distributions(target, launches, weights).items()
    }


def forecast(case):
    anchor = log_count(case['known'][0]['viewers'])
    logs = hourly_logs(case['history_hourly'], clock(case['day_start']))
    target = checkpoint(logs, anchor, 'rolling')  # Both kit phases use midnight-local console launches.
    growth = predict_growth(target, read_launches(case))
    return {'hours': [
        {'hour': h + 1, 'q': [round(math.exp(max(0.0, min(LOG_LIMIT, anchor + delta))), 3) for delta in growth[h]]}
        for h in HOURS
    ]}


def main():
    inputs = Path(os.environ.get('MAKEFUN_INPUT_DIR', '/input'))
    outputs = Path(os.environ.get('MAKEFUN_OUTPUT_DIR', '/output'))
    outputs.mkdir(parents=True, exist_ok=True)
    for source in sorted(inputs.glob('*.in')):
        case = json.loads(source.read_bytes())
        prediction = json.dumps(forecast(case), separators=(',', ':'), allow_nan=False)
        (outputs / source.with_suffix('.out').name).write_bytes((prediction + '\n').encode('utf-8'))


if __name__ == '__main__':
    main()
