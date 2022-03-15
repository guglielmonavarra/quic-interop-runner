"""Some exceptions."""

from enum import Enum


class ErrorCode(Enum):
    """Error codes for failed experiments."""

    UNSUPPORTED_TEST_CASE = "UNSUPPORTED_TEST_CASE"
    UNKNOWN_ERROR = "UNKNOWN_ERROR"
    TIMEOUT = "TIMEOUT"
    TOO_MANY_VERSIONS = "TOO_MANY_VERSIONS"
    INVALID_VERSION = "INVALID_VERSION"
    EXTRA_DOWNLOADED_FILES = "EXTRA_DOWNLOADED_FILES"
    MISSING_DOWNLOADED_FILES = "MISSING_DOWNLOADED_FILES"
    DOWNLOADED_FILE_SIZE_MISSMATCH = "DOWNLOADED_FILE_SIZE_MISSMATCH"
    DOWNLOADED_FILE_CONTENT_MISSMATCH = "DOWNLOADED_FILE_CONTENT_MISSMATCH"
    HANDSHAKE_AMOUNT_MISSMATCH = "HANDSHAKE_AMOUNT_MISSMATCH"
    NO_DCID = "NO_DCID"
    NO_MATCHING_SCID = "NO_MATCHING_SCID"
    UNEXPECTED_RETRY = "UNEXPECTED_RETRY"
    TOO_LESS_CLIENT_HELLOS = "TOO_LESS_CLIENT_HELLOS"
    STREAM_LIMIT_TOO_HIGH = "STREAM_LIMIT_TOO_HIGH"
    COULD_NOT_CHECK_STREAM_LIMIT = "COULD_NOT_CHECK_STREAM_LIMIT"
    RETRY_PACKET_WITHOUT_RETRY_TOKEN = "RETRY_PACKET_WITHOUT_RETRY_TOKEN"
    NO_RETRY_PACKET = "NO_RETRY_PACKET"
    PACKET_NUMBER_RESETTED = "PACKET_NUMBER_RESETTED"
    NO_RETRY_PACKET_WITH_RETRY_TOKEN = "NO_RETRY_PACKET_WITH_RETRY_TOKEN"
    CERT_MESSAGE_IN_SECOND_HANDSHAKE = "CERT_MESSAGE_IN_SECOND_HANDSHAKE"
    DANGLING_HANDSHAKE_PACKET = "DANGLING_HANDSHAKE_PACKET"
    NO_CERT_MESSAGE_IN_FIRST_HANDSHAKE = "NO_CERT_MESSAGE_IN_FIRST_HANDSHAKE"
    NO_0RTT_DATA = "NO_0RTT_DATA"
    TOO_MUCH_1RTT_DATA = "TOO_MUCH_1RTT_DATA"
    TOO_LITTLE_HANDSHAKE_CRYPTO_DATA = "TOO_LITTLE_HANDSHAKE_CRYPTO_DATA"
    UNEXPECTED_VERSION_NEGOTIATION_PACKET = "UNEXPECTED_VERSION_NEGOTIATION_PACKET"
    INVALID_PACKET_TYPE = "INVALID_PACKET_TYPE"
    UNKNOWN_SENDER = "UNKNOWN_SENDER"
    AMPLIFICATION_ERROR = "AMPLIFICATION_ERROR"
    CRYPTO_ERROR = "CRYPTO_ERROR"
    ECN_ERROR = "ECN_ERROR"
    MISSING_PATH_CHALLENGE_FRAME = "MISSING_PATH_CHALLENGE_FRAME"
    TOO_FEW_PATH_CHALLENGE_FRAMES = "TOO_FEW_PATH_CHALLENGE_FRAMES"
    MISSING_PATH_RESPONSE = "MISSING_PATH_RESPONSE"
    IPV4_PACKETS_IN_TRACE = "IPV4_PACKETS_IN_TRACE"
    REUSING_OLD_DCID = "REUSING_OLD_DCID"
    NO_TIME_DIFFERENCE = "NO_TIME_DIFFERENCE"
    BROKEN_PCAP = "BROKEN_PCAP"


class TestFailed(Exception):
    """Exception for failed test cases."""

    def __init__(self, msg: str, error_code: ErrorCode):
        super().__init__(msg)
        self.error_code = error_code


class TestUnsupported(Exception):
    pass


class ConflictError(Exception):
    pass
