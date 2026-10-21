"""Fragment extractor - extract reusable fragments from existing templates."""


class FragmentExtractor:
    """Extract independent animation scenes from templates as fragments."""

    def extract(self, template_path: str, scene_range: tuple[int, int], output_dir: str) -> str:
        """Extract a scene range from template as a fragment.

        Returns path to extracted fragment directory.
        """
        raise NotImplementedError
