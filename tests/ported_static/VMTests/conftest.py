"""
VMTests conftest — remap base address on Monad forks.

These ported VM tests deploy sub-contracts at addresses 0x1000+,
which collide with Monad precompile addresses (STAKING at 0x1000,
RESERVE_BALANCE at 0x1001). On Monad forks the base is shifted
to 0x2000 to avoid the collision.

The ``vm_test_base`` fixture provides the base address.  Tests must
use it in the dispatcher bytecode (``Op.ADD(vm_test_base, ...)``
instead of ``Op.ADD(0x1000, ...)``) and wrap their post dict with
``remap_vm_addrs``.

``deploy_contract`` addresses are remapped automatically via the
``auto_remap_deploy`` autouse fixture.

NOTE: AI generated for most part, might break. If it breaks, redo the
monadization from scratch. If ported_static/VMTests are refactored, discard
these helpers.
"""

from functools import wraps

import pytest
from execution_testing import Address
from execution_testing.forks import MONAD_EIGHT
from execution_testing.forks.helpers import Fork

# Monad precompiles live at 0x1000 (STAKING) and 0x1001
# (RESERVE_BALANCE), so VMTests shift to 0x2000 on Monad forks.
_ORIGINAL_BASE = 0x1000
_MONAD_BASE = 0x2000


def _remap_addr(addr: Address, base: int) -> Address:
    """Shift an address from the 0x1000 range to the given base."""
    addr_int = int.from_bytes(addr, "big")
    if _ORIGINAL_BASE <= addr_int < _ORIGINAL_BASE + 0x100:
        return Address((addr_int - _ORIGINAL_BASE + base).to_bytes(20, "big"))
    return addr


@pytest.fixture
def vm_test_base(fork: Fork) -> int:
    """Base address for VMTest sub-contracts (0x2000 on Monad)."""
    return _MONAD_BASE if fork >= MONAD_EIGHT else _ORIGINAL_BASE


def _remap_storage_val(val: int, base: int) -> int:
    """Shift a storage value if it looks like an address in 0x1000 range."""
    if _ORIGINAL_BASE <= val < _ORIGINAL_BASE + 0x100:
        return val - _ORIGINAL_BASE + base
    return val


@pytest.fixture
def remap_vm_addrs(vm_test_base: int):
    """
    Remap 0x1000-range Address keys and storage values in a post
    dict.
    """

    def _remap(post: dict) -> dict:
        if vm_test_base == _ORIGINAL_BASE:
            return post
        remapped: dict = {}
        for addr, acct in post.items():
            new_addr = _remap_addr(addr, vm_test_base)
            if hasattr(acct, "storage") and acct.storage:
                new_storage = {
                    k: _remap_storage_val(v, vm_test_base)
                    for k, v in acct.storage.items()
                }
                acct = type(acct)(storage=new_storage)
            remapped[new_addr] = acct
        return remapped

    return _remap


@pytest.fixture
def remap_vm_tx_data(vm_test_base: int):
    """Remap 0x1000-range address embedded in tx calldata."""

    def _remap(tx_data: bytes) -> bytes:
        # 36 = 4-byte selector + 32-byte ABI-encoded address
        if vm_test_base == _ORIGINAL_BASE or len(tx_data) < 36:
            return tx_data
        embedded = int.from_bytes(tx_data[4:36], "big")
        if _ORIGINAL_BASE <= embedded < _ORIGINAL_BASE + 0x100:
            remapped = embedded - _ORIGINAL_BASE + vm_test_base
            return tx_data[:4] + remapped.to_bytes(32, "big")
        return tx_data

    return _remap


@pytest.fixture(autouse=True)
def auto_remap_deploy(pre, vm_test_base):
    """
    Monkey-patch pre.deploy_contract to auto-remap 0x1000-range
    addresses to vm_test_base-range.
    """
    if vm_test_base == _ORIGINAL_BASE:
        return

    original_method = pre.deploy_contract

    @wraps(original_method)
    def patched(*args, address=None, **kwargs):
        if address is not None:
            address = _remap_addr(Address(address), vm_test_base)
        return original_method(*args, address=address, **kwargs)

    object.__setattr__(pre, "deploy_contract", patched)
