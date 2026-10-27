"""Fragment combiner - compose multiple fragments into a new template."""

from __future__ import annotations


class FragmentCombiner:
    """Combine fragments on a timeline to create a new template."""

    def combine(self, fragments: list[dict], output_path: str) -> str:
        """Combine fragments into a new .motion template.

        Each fragment dict should have: id, path, start_time, duration, params
        Returns path to combined template.
        """
        raise NotImplementedError
