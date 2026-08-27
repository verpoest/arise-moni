#!/usr/bin/env python3
"""Check the White Rabbit timestamps in a TAXI .bin file.

Each TAXI file carries two kinds of timestamp: the free-running RTC counter
(8.4211 ns/tick, not wall-clock) and the absolute White Rabbit timestamps in
0x8000 blocks, which encode day-of-year and second-of-day. Both are present in
every WR block, sampled at the same instant.

Two independent things are checked:

1. START:  the first WR blocks must agree with the day/time in the filename
           (e.g. s6_eventData_1770292829_2026-02-05_12-00-29.bin -> day 36,
           43229 s). This catches a WR that has lost lock or sits on the wrong
           date. Note the filename marks the START of the file, so only the
           first blocks can be compared with it.
2. SPAN:   across the whole file the WR seconds must keep step with the RTC,
           which free-runs regardless of what the WR does. For every block,
           (WR - WR_first) - (RTC - RTC_first) must stay near zero. This
           catches a WR that stalls or jumps partway through a file, which the
           start check alone cannot see.

Reading every block costs ~0.2 s for an hourly file, so the whole file is
checked by default. Individual blocks are occasionally corrupt -- a garbled day
or second-of-day word shows up as a wild outlier -- so both verdicts use MEDIAN
drift plus a minimum count AND fraction of out-of-tolerance blocks. A handful of
bad blocks never raises an alert. The default 300 s tolerance is also far above
the known nightly ~15-25 s WR re-sync stall, which is not a DAQ fault and must
not page anyone.

Exit codes (so the health monitors can branch on it):
    0  WR timestamps present, match the filename and keep step with the RTC
    1  WR timestamps present but wrong (unsynced clock, or stalled/jumped)
    2  could not evaluate -- unparseable filename or unreadable/missing file;
       the caller should treat this as neutral, not an alert
    3  the file contains no WR (0x8000) blocks at all. In a file that is still
       being written this can be normal; in a closed, fully written file it
       means the WR is not delivering timestamps, and the caller should alert.
"""

import argparse
import calendar
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import utils

RTC_TICK_S = 8.4211e-9  # TAXI free-running counter period
N_START_BLOCKS = 5      # blocks compared against the filename


def secs_into_year(day_of_year, second_of_day):
    """Seconds since the start of the (1-indexed) year; lets us diff WR vs
    filename across day/month boundaries with a single subtraction."""
    return (day_of_year - 1) * 86400 + second_of_day


def seconds_in_year(year):
    return (366 if calendar.isleap(year) else 365) * 86400


def unwrap_year_boundary(drift, year):
    """Fold a drift that straddles New Year back into a sane range.

    The WR blocks carry a day-of-year but no year, so a file opened in the last
    seconds of December whose first WR block already belongs to January reads as
    a ~365 day drift (and vice versa at a New Year DAQ restart). Anything beyond
    half a year is really such a wrap; a genuine fault of that size stays far
    outside tolerance after folding, so this cannot mask one.
    """
    if drift < -seconds_in_year(year) / 2:
        return drift + seconds_in_year(year)
    if drift > seconds_in_year(year) / 2:
        return drift - seconds_in_year(year - 1)
    return drift


def median(values):
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def count_bad(deviations, tol_s, max_bad_frac, min_bad_count):
    """(n_bad, verdict) for a list of deviations from the expected value.

    A verdict of True needs both a minimum count and a minimum fraction, so
    neither a couple of corrupt words in a long file nor a short sample of a
    just-opened file can raise an alert on its own.
    """
    n_bad = sum(1 for d in deviations if abs(d) > tol_s)
    bad = (n_bad >= min_bad_count
           and n_bad / len(deviations) > max_bad_frac)
    return n_bad, bad


def check(path, n=None, tol_s=300, max_bad_frac=0.05, min_bad_count=3):
    meta = utils.parse_filename_info(path)
    if not meta:
        print(f"{os.path.basename(path)}: unparseable filename", file=sys.stderr)
        return 2

    dt = meta['datetime']
    file_ref = secs_into_year(
        int(dt.strftime('%j')), dt.hour * 3600 + dt.minute * 60 + dt.second
    )

    blocks = utils.get_wr_blocks(path, n=n)
    if blocks is None:
        print(f"{os.path.basename(path)}: file unreadable", file=sys.stderr)
        return 2
    if not blocks:
        print(f"{meta['station']} {os.path.basename(path)}: "
              f"no WR (0x8000) timestamps in file")
        return 3

    wr_secs = [secs_into_year(day, sod) for day, sod, _ in blocks]
    rtc = [ticks for _, _, ticks in blocks]

    # 1. START: first blocks vs the filename (median guards one corrupt word).
    #    Each block's own RTC offset is subtracted so that the seconds elapsed
    #    since the file opened do not count as drift -- otherwise the sampled
    #    blocks carry a bias of one WR cadence each, which at the slowest DAQ
    #    cadence (48 s) would eat a third of the tolerance.
    head = [unwrap_year_boundary(sec - file_ref - (t - rtc[0]) * RTC_TICK_S,
                                 dt.year)
            for sec, t in zip(wr_secs[:N_START_BLOCKS], rtc[:N_START_BLOCKS])]
    start_drift = median(head)
    start_bad = abs(start_drift) > tol_s

    # 2. SPAN: WR must advance with the free-running RTC. Deviations are taken
    #    about their own median so that a corrupt first block shifts nothing.
    span_dev = [(s - wr_secs[0]) - (t - rtc[0]) * RTC_TICK_S
                for s, t in zip(wr_secs, rtc)]
    span_ref = median(span_dev)
    span_dev = [d - span_ref for d in span_dev]
    n_span_bad, span_bad = count_bad(span_dev, tol_s, max_bad_frac, min_bad_count)
    worst_span = max(span_dev, key=abs)

    verdict = 'MISMATCH' if (start_bad or span_bad) else 'ok'
    print(f"{meta['station']} file={dt:%Y-%m-%d %H:%M:%S} "
          f"n={len(blocks)} start_drift={start_drift:+.0f}s "
          f"span_dev_max={worst_span:+.1f}s out_of_tol={n_span_bad}/{len(blocks)} "
          f"tol={tol_s}s -> {verdict}"
          + (" [start]" if start_bad else "")
          + (" [span]" if span_bad else ""))
    return 1 if (start_bad or span_bad) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check the White Rabbit (0x8000) timestamps in a TAXI .bin "
                    "file against the filename and against the RTC.")
    parser.add_argument("file", help="Path to the .bin file")
    parser.add_argument("-n", type=int, default=None,
                        help="Number of WR blocks to read (default: all)")
    parser.add_argument("--tolerance", type=int, default=300,
                        help="Max allowed drift in seconds (default 300)")
    args = parser.parse_args()

    if not os.path.exists(args.file):
        # Neutral, not an alert: the file can rotate away between the caller
        # picking it and this check running.
        print(f"File not found: {args.file}", file=sys.stderr)
        sys.exit(2)

    sys.exit(check(args.file, n=args.n, tol_s=args.tolerance))
