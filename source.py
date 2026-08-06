"""
The base class both vessel data sources inherit from.

What lives here is only what the sources genuinely share: reading credentials
out of a .env file, and reporting progress. Deliberately absent is a common
`fetch()` method.

That absence is the design decision worth explaining. It is tempting to give
GFWSource and LiveMapSource a shared fetch(region, ...) so they look
interchangeable, but they are not. Global Fishing Watch answers questions
about a date range and hands back aggregated statistics; the MarineTraffic
live map has no history at all and answers "what is there now" at a given map
zoom. A shared method would have to carry start_date and end_date that the
live map cannot honour, and a zoom that means nothing to GFW. Each subclass
therefore keeps the method signature that is honest for it, and main.py picks
the source it wants rather than treating them as swappable.
"""

import os
import sys

# Both MarineTraffic endpoints -- the live map (livemap.py) and the reports
# page (data_get.py) -- require this header. Without it the site answers with
# an HTML error page instead of JSON. Recorded once here so the quirk can't be
# fixed in one file and missed in the other.
MARINETRAFFIC_HEADERS = {
    "X-Requested-With": "XMLHttpRequest",
    "Accept": "application/json, text/plain, */*",
}


class VesselDataSource:
    """
    Common behaviour for a source of vessel data.

    Subclasses are GFWSource (gfw.py) and LiveMapSource (livemap.py). They
    share credential loading and progress reporting, nothing more.

    Attributes:
        verbose (bool): Whether progress messages are printed to stderr.
    """

    def __init__(self, verbose=True):
        """
        Args:
            verbose (bool): Print progress to stderr. Defaults to True.
        """
        self.verbose = verbose

    def log(self, message):
        """
        Report progress to stderr, unless the source is quiet.

        stderr rather than stdout so that progress never mixes into piped
        data output.

        Args:
            message (str): The line to print.

        Returns:
            None
        """
        if self.verbose:
            print(message, file=sys.stderr)

    @staticmethod
    def load_dotenv(path=".env"):
        """
        Load KEY=value pairs from a .env file into the environment.

        Blank lines and lines starting with # are skipped, and surrounding
        quotes are stripped from values. Existing environment variables win,
        so an exported value overrides the file. Does nothing if the file is
        absent.

        Args:
            path (str): Path to the .env file. Defaults to ".env" in the
                working directory.

        Returns:
            None
        """
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))

    def require_env(self, name, help_text):
        """
        Fetch a required credential from the environment, or exit helpfully.

        Reads .env first. A missing credential is a normal setup step rather
        than a bug, so this exits with an explanation instead of raising.

        Args:
            name (str): Environment variable to read, e.g.
                "GFW_API_ACCESS_TOKEN".
            help_text (str): What to tell the user about getting one.

        Returns:
            str: The credential.

        Raises:
            SystemExit: If the variable is unset or empty.
        """
        self.load_dotenv()
        value = os.environ.get(name)
        if not value:
            sys.exit(f"{name} is not set.\n{help_text}")
        return value
