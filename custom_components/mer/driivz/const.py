"""Constants for the Driivz driver-portal client."""

from __future__ import annotations

DEFAULT_BASE_URL = "https://driver.uk.mer.eco"

HEADER_CSRF = "X-CSRF-TOKEN"
HEADER_APP_TYPE = "X-APP-TYPE"
HEADER_JSON_TYPES = "X-JSON-TYPES"
HEADER_AJAX = "X-Ajax-call"
HEADER_RATE_LIMIT_REMAINING = "X-Rate-Limit-Remaining"

PATH_LOGIN = "login"
PATH_LOGOUT = "logout"
PATH_MAP = "findCharger"
PATH_FIND_SITES_IN_BOUNDS = "stationFacade/findSitesInBounds"
PATH_FIND_STATIONS_IN_BOUNDS = "stationFacade/findStationsInBounds"
PATH_FIND_STATIONS_BY_IDS = "stationFacade/findStationsByIds"
PATH_FIND_STATION_BY_ID = "stationFacade/findStationById"
PATH_LAST_ACTIVE_SOCKET = "stationFacade/findLastActiveChargeSocket"
PATH_TRANSACTION_START_TIME = "stationFacade/findCurrentTransactionStartTime"
PATH_TRANSACTION_ESTIMATE = "stationFacade/findCurrentTransactionBillingChargingEstimation"
PATH_START_CHARGE = "stationFacade/startChargeNow"
PATH_STOP_CHARGE = "stationFacade/stopCharge"
PATH_WALLET = "billingFacade/findCustomerDetailWalletByCustomerId"
PATH_TRANSACTIONS = "customerFacade/findDriverChargeTransactionLogByView"

STATUS_AVAILABLE = "AVAILABLE"
STATUS_OCCUPIED = "OCCUPIED"
STATUS_CHARGING = "CHARGING"
STATUS_DISCHARGING = "DISCHARGING"
STATUS_PAUSED = "PAUSED"
STATUS_PREPARING = "PREPARING"
STATUS_FINISHING = "FINISHING"
STATUS_RESERVED = "RESERVED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_FAULTED = "FAULTED"
STATUS_UNKNOWN = "UNKNOWN"

STATUSES: tuple[str, ...] = (
    STATUS_AVAILABLE,
    STATUS_OCCUPIED,
    STATUS_CHARGING,
    STATUS_DISCHARGING,
    STATUS_PAUSED,
    STATUS_PREPARING,
    STATUS_FINISHING,
    STATUS_RESERVED,
    STATUS_UNAVAILABLE,
    STATUS_FAULTED,
    STATUS_UNKNOWN,
)

IN_USE_STATUSES: frozenset[str] = frozenset(
    {
        STATUS_OCCUPIED,
        STATUS_CHARGING,
        STATUS_DISCHARGING,
        STATUS_PAUSED,
        STATUS_PREPARING,
        STATUS_FINISHING,
    }
)

AUTH_ERROR_TYPES: frozenset[str] = frozenset(
    {
        "ACCESS_DENIED",
        "AUTHENTICATION_REQUIRED",
        "BAD_CREDENTIALS",
        "LOGIN_PAGE",
        "PASSWORD_EXPIRES",
        "SESSION_EXPIRED",
        "USER_IS_NOT_ACTIVE",
        "USER_NOT_LOGGED_IN",
    }
)

OPERATION_PENDING = "PENDING"
