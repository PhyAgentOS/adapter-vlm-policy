class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)

    def to_dict(self):
        return {"code": self.code, "message": self.message}
