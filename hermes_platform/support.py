"""Runtime support policy, separate from host recognition and remote targets."""
import sys


def require_supported_host(platform: str | None = None) -> None:
    """Reject native Windows; WSL runs on the supported Linux interpreter."""
    target = sys.platform if platform is None else platform
    if target not in {"linux", "darwin", "android"}:
        raise RuntimeError("Hermes supports Linux and macOS. On Windows, run Hermes inside WSL.")
