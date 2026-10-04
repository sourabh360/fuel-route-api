from dataclasses import dataclass

import numpy as np

PASS, BUY, FIRST = 0, 1, 2


class NoFuelPlanError(Exception):
    pass


@dataclass
class Candidate:
    station_id: int
    mile: float
    off_route: float
    price: float


@dataclass
class Purchase:
    candidate: Candidate
    gallons: float

    @property
    def cost(self):
        return self.gallons * self.candidate.price


def plan_fuel_stops(candidates, total_miles, tank_range, mpg, start_window_miles, stop_penalty=0.0):
    total = int(round(total_miles))
    if total <= 0:
        return []

    tank = int(tank_range)
    window = min(start_window_miles, total_miles)

    stations = sorted((c for c in candidates if c.mile < total_miles), key=lambda c: (c.mile, c.price))
    if not any(s.mile <= window for s in stations):
        raise NoFuelPlanError(f"No fuel station within {start_window_miles:g} miles of the start of the route.")

    pos = np.array([int(round(s.mile)) for s in stations])
    n = len(stations)
    fuel = np.arange(tank + 1)

    choice = np.zeros((n, tank + 1), dtype=np.int8)
    fuel_before = np.zeros((n, tank + 1), dtype=np.int16)
    leave = None

    for i, station in enumerate(stations):
        price = station.price / mpg

        arrive = np.full(tank + 1, np.inf)
        if leave is not None:
            gap = pos[i] - pos[i - 1]
            if gap <= tank:
                arrive[:tank + 1 - gap] = leave[gap:]

        best = arrive.copy()
        how = np.full(tank + 1, PASS, dtype=np.int8)
        start_level = fuel.astype(np.int16)

        adjusted = arrive - fuel * price
        lowest = np.minimum.accumulate(adjusted)
        lowest_at = np.maximum.accumulate(np.where(adjusted <= lowest, fuel, 0))
        buying = np.full(tank + 1, np.inf)
        buying[1:] = stop_penalty + fuel[1:] * price + lowest[:-1]

        cheaper = buying < best
        best[cheaper] = buying[cheaper]
        how[cheaper] = BUY
        start_level[1:][cheaper[1:]] = lowest_at[:-1][cheaper[1:]]

        if station.mile <= window:
            first_fill = stop_penalty + (pos[i] + fuel) * price
            cheaper = first_fill < best
            best[cheaper] = first_fill[cheaper]
            how[cheaper] = FIRST

        choice[i] = how
        fuel_before[i] = start_level
        leave = best

    last_leg = total - pos[-1]
    if last_leg > tank or not np.isfinite(leave[last_leg:]).any():
        raise NoFuelPlanError(gap_message(pos, total, tank))

    level = last_leg + int(np.argmin(leave[last_leg:]))
    bought = {}
    for i in range(n - 1, -1, -1):
        if choice[i][level] == BUY:
            before = int(fuel_before[i][level])
            bought[i] = level - before
            level = before
        elif choice[i][level] == FIRST:
            bought[i] = level + int(pos[i])
            break
        if i > 0:
            level += int(pos[i] - pos[i - 1])

    return [Purchase(stations[i], bought[i] / mpg) for i in sorted(bought)]


def gap_message(pos, total, tank):
    points = np.concatenate([pos, [total]])
    k = int(np.argmax(np.diff(points)))
    return (f"No fuel station reachable between mile {points[k]} and mile {points[k + 1]}; "
            f"the gap exceeds the {tank}-mile range.")
