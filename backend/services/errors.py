"""Exceptions that routes raise to short-circuit with a JSON error. The handler is
registered in app.create_app, so a validator can just ``raise ApiError(...)``."""


class ApiError(Exception):
    def __init__(self, message, status=400, code=None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.code = code

    def to_response(self):
        body = {'error': self.message}
        if self.code:
            body['code'] = self.code
        return body, self.status
