"""Direct Mode harness for contracts/bounty_acceptance.py (see support.py for
what is and is not mocked). Validator logic is exercised through direct_vm.run_validator(),
which hands the captured validator closure a forged leader result while the
mocks stand in for the validator's own view of the web and the model."""

import os
import sys

import pytest

from tests.direct.support import CONTRACT, NOW


# -- Windows compatibility shim for genlayer-test 0.29.2 ----------------------
#
# The official direct runner injects the transaction message by writing it to a
# temp file, dup2-ing that file onto fd 0 and unlinking the path while fd 0
# still references it. POSIX permits that; Windows refuses with WinError 32, so
# every direct test errors at deploy on a fresh Windows checkout. The shim
# tolerates that one refusal and changes nothing else. It is a no-op on Linux
# and macOS, so CI runs the runner as published.

def _tolerate_windows_unlink():
    if os.name != "nt":
        return
    try:
        from gltest.direct import loader as _loader
    except ImportError:
        return
    original = _loader._inject_message_to_fd0
    if getattr(original, "_shim", False):
        return

    def inject_tolerant(vm):
        real_unlink = os.unlink

        def unlink_tolerant(path, *args, **kwargs):
            try:
                real_unlink(path, *args, **kwargs)
            except PermissionError:
                pass
        os.unlink = unlink_tolerant
        try:
            return original(vm)
        finally:
            os.unlink = real_unlink

    inject_tolerant._shim = True
    _loader._inject_message_to_fd0 = inject_tolerant


_tolerate_windows_unlink()


# -- make warp() move the transaction clock -----------------------------------
#
# direct_vm.warp() sets the block timestamp the SDK's datetime.now() reads, but
# leaves gl.message_raw["datetime"] - the transaction time every window in this
# contract is measured against - at whatever the deploy injected. Without this,
# no test could cross a window. The shim only keeps the two in step.

def _warp_moves_the_message_clock():
    try:
        from gltest.direct.vm import VMContext
    except ImportError:
        return
    original = VMContext.warp
    if getattr(original, "_shim", False):
        return

    def warp(self, timestamp: str) -> None:
        original(self, timestamp)
        gl = sys.modules.get("genlayer.gl")
        if gl is not None and getattr(gl, "message_raw", None) is not None:
            gl.message_raw["datetime"] = timestamp

    warp._shim = True
    VMContext.warp = warp


_warp_moves_the_message_clock()


class Bank:
    """The chain's side of the money, which Direct Mode does not model: the
    contract's balance and each wallet's receipts. A call that carries value
    credits the contract whether or not the call succeeds - as the network does -
    so a write that raises with value attached strands it here, visibly."""

    def __init__(self):
        self.contract = 0
        self.received = {}
        self.transfers = []

    def deposit(self, amount: int):
        self.contract += amount

    def sent(self, wallet: str, amount: int):
        self.contract -= amount
        self.received[wallet] = self.received.get(wallet, 0) + amount
        self.transfers.append((wallet, amount))


class Guarded:
    """The deployed contract, with the one thing the direct runner leaves out:
    a write that raises leaves no state behind. Without this a suite passes over
    a path that, live, reverts a refund and keeps the money."""

    def __init__(self, contract, vm):
        object.__setattr__(self, "_contract", contract)
        object.__setattr__(self, "_vm", vm)

    def __getattr__(self, name):
        target = getattr(self._contract, name)
        if not callable(target):
            return target
        vm = self._vm

        def call(*args, **kwargs):
            snapshot = vm.snapshot()
            sent_before = len(vm.bank.transfers)
            try:
                return target(*args, **kwargs)
            except BaseException:
                vm.revert(snapshot)
                # the transfers a reverted call emitted never happened
                for wallet, amount in vm.bank.transfers[sent_before:]:
                    vm.bank.contract += amount
                    vm.bank.received[wallet] -= amount
                del vm.bank.transfers[sent_before:]
                raise
        return call


@pytest.fixture
def board(direct_vm, direct_deploy):
    direct_vm.check_pickling = True
    direct_vm.warp(NOW)
    bank = Bank()
    direct_vm.bank = bank

    def hook(vm, request):
        if isinstance(request, dict) and "EthSend" in request:
            send = request["EthSend"]
            bank.sent("0x" + send["address"].as_bytes.hex(), int(send["value"]))
            return {"ok": None}
        return None

    direct_vm._gl_call_hook = hook
    return Guarded(direct_deploy(CONTRACT), direct_vm)


@pytest.fixture
def mod(board):
    """The loaded contract module: pure helpers are tested through it."""
    for name, module in sys.modules.items():
        if name.endswith("bounty_acceptance") and hasattr(module, "_parse_bounty"):
            return module
    raise AssertionError("the contract module is not loaded")
