"""Small explicit-session client for authoring scripts using the native controller.

The caller chooses each operation. This client never retries edits or replaces
the user's selection after a revision/session conflict.
"""
from server import command


class AuthoringSession:
    def __init__(self):
        self.status = command('revision')

    def read(self, method, **params):
        result = command(method, params, expected_session=self.status['session'])
        if result['session'] != self.status['session']:
            raise RuntimeError('Editor restarted; establish a new authoring session')
        return result

    def edit(self, method, **params):
        result = command(method, params, self.status['documentRevision'], response='summary', expected_session=self.status['session'])
        self.status = result
        return result

    def refresh(self):
        """Explicitly accept the currently observed document after inspecting a change."""
        self.status = command('revision')
        return self.status
