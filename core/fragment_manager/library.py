"""Fragment library - store, search, and manage reusable animation fragments."""


class FragmentLibrary:
    """Library of reusable animation fragments."""

    def __init__(self, library_dir: str):
        self.library_dir = library_dir

    def list(self, category: str = None) -> list[dict]:
        """List fragments, optionally filtered by category."""
        raise NotImplementedError

    def search(self, query: str) -> list[dict]:
        """Search fragments by keywords and tags."""
        raise NotImplementedError

    def get(self, fragment_id: str) -> dict:
        """Get fragment metadata by ID."""
        raise NotImplementedError
