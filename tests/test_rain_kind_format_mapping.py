"""Rain-gage Format must map to the correct rain_kind (review P1-9).

Before the fix, only CUMULATIVE was recognized on inline gages and VOLUME was
dropped to ``rain_kind=None``, which the plot layer then defaulted to
``depth_mm_per_dt`` and re-scaled by dt/60 as if it were intensity. A
``VOLUME`` step of 3 mm rendered as 0.25 mm. These tests pin the mapping at
the parser so the plot layer receives the right unit.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agentic_swmm.agent.swmm_runtime.inp_parsing import rainfall_timeseries_options


def _kind_for(inp_text: str) -> str | None:
    with tempfile.TemporaryDirectory() as raw:
        inp = Path(raw) / "model.inp"
        inp.write_text(inp_text, encoding="utf-8")
        options = rainfall_timeseries_options(inp)
        assert options, "expected at least one rainfall option"
        # The gage-referenced series is the one the plot layer would pick.
        chosen = next((o for o in options if o.get("used_by_raingage")), options[0])
        return chosen.get("rain_kind")


_INLINE = """[RAINGAGES]
RG1  {fmt}  0:15  1.0  TIMESERIES  TS_RAIN

[TIMESERIES]
TS_RAIN  06/01/2022 00:00  3.0
TS_RAIN  06/01/2022 00:15  0.0
"""

_FILE = """[RAINGAGES]
rg1  {fmt}  0:15  1.0  FILE  "storm.dat"
"""


class RainKindFormatMappingTests(unittest.TestCase):
    def test_inline_volume_is_depth_not_intensity(self) -> None:
        self.assertEqual(_kind_for(_INLINE.format(fmt="VOLUME")), "depth_mm_per_dt")

    def test_inline_intensity(self) -> None:
        self.assertEqual(_kind_for(_INLINE.format(fmt="INTENSITY")), "intensity_mm_per_hr")

    def test_inline_cumulative(self) -> None:
        self.assertEqual(_kind_for(_INLINE.format(fmt="CUMULATIVE")), "cumulative_depth_mm")

    def test_file_volume_is_depth(self) -> None:
        self.assertEqual(_kind_for(_FILE.format(fmt="VOLUME")), "depth_mm_per_dt")

    def test_file_intensity(self) -> None:
        self.assertEqual(_kind_for(_FILE.format(fmt="INTENSITY")), "intensity_mm_per_hr")


if __name__ == "__main__":
    unittest.main()
