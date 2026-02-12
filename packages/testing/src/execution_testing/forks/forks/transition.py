"""List of all transition fork definitions."""

from ..transition_base_fork import transition_fork
from .forks import (
    BPO1,
    BPO2,
    BPO3,
    BPO4,
    MONAD_EIGHT,
    MONAD_NEXT,
    Berlin,
    Cancun,
    London,
    Osaka,
    Paris,
    Prague,
    Shanghai,
)


# Transition Forks
@transition_fork(to_fork=London, at_block=5)
class BerlinToLondonAt5(Berlin):
    """Berlin to London transition at Block 5."""

    pass


@transition_fork(to_fork=Shanghai, at_timestamp=15_000)
class ParisToShanghaiAtTime15k(Paris):
    """Paris to Shanghai transition at Timestamp 15k."""

    pass


@transition_fork(to_fork=Cancun, at_timestamp=15_000)
class ShanghaiToCancunAtTime15k(Shanghai):
    """Shanghai to Cancun transition at Timestamp 15k."""

    pass


@transition_fork(to_fork=Prague, at_timestamp=15_000)
class CancunToPragueAtTime15k(Cancun):
    """Cancun to Prague transition at Timestamp 15k."""

    pass


@transition_fork(to_fork=Osaka, at_timestamp=15_000)
class PragueToOsakaAtTime15k(Prague):
    """Prague to Osaka transition at Timestamp 15k."""

    pass


@transition_fork(to_fork=MONAD_EIGHT, at_timestamp=15_000)
class PragueToMONAD_EIGHTAtTime15k(Prague):
    """Prague to MONAD_EIGHT transition at Timestamp 15k."""

    pass


@transition_fork(to_fork=MONAD_NEXT, at_timestamp=15_000)
class MONAD_EIGHTToMONAD_NEXTAtTime15k(MONAD_EIGHT):
    """Prague to MONAD_NEXT transition at Timestamp 15k."""

    pass


@transition_fork(to_fork=BPO1, at_timestamp=15_000)
class OsakaToBPO1AtTime15k(Osaka):
    """Osaka to BPO1 transition at Timestamp 15k."""

    pass


@transition_fork(to_fork=BPO2, at_timestamp=15_000)
class BPO1ToBPO2AtTime15k(BPO1):
    """BPO1 to BPO2 transition at Timestamp 15k."""

    pass


@transition_fork(to_fork=BPO3, at_timestamp=15_000)
class BPO2ToBPO3AtTime15k(BPO2):
    """BPO2 to BPO3 transition at Timestamp 15k."""

    pass


@transition_fork(to_fork=BPO4, at_timestamp=15_000)
class BPO3ToBPO4AtTime15k(BPO3):
    """BPO3 to BPO4 transition at Timestamp 15k."""

    pass
