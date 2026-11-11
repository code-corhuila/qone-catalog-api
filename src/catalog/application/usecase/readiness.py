"""The ready probe (HU-PLT-004, ADR-011): the service takes traffic only when every dependency
answers. Liveness (`/health`) stays a constant answer; readiness asks each probe, which keeps its
own short timeout, and names the first dependency that does not answer."""

from catalog.application.errors import Unavailable
from catalog.application.port.outbound.ports import DependencyProbe


class Readiness:
    def __init__(self, probes: list[DependencyProbe]) -> None:
        self._probes = probes

    def check(self) -> dict[str, str]:
        for probe in self._probes:
            if not probe.reachable():
                raise Unavailable(f"{probe.name} is not reachable")
        return {probe.name: "ok" for probe in self._probes}
