"""Lifecycle boundary for the single supported game studio."""

from .studio import GameStudio


class Service:
    def __init__(self, directory):
        self.studio = GameStudio(directory)

    def start(self):
        """Model processes start lazily on the first request."""

    def stop(self):
        self.studio.close()
