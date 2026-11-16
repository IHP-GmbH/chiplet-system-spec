# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 IHP GmbH
"""The authoring surface: :class:`SystemBuilder`.

The one mutable object in the repo. It accumulates technologies, die instances,
links, ties and supply nets, and freezes into an immutable :class:`System` at ``build()``.
``connect()`` validates eagerly, at the call site, so an interface mismatch
fires before any physical step. It reuses the same pure check
functions ``System.__post_init__`` runs (``resolve_port``,
``check_interface_compatible`` from :mod:`chiplet_system_description.model`), so a check is
written once and both entry paths agree.
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple, Union

from .errors import DuplicateId, PortAlreadyBound
from .model import (
    DieInstance,
    InterconnectMethod,
    Interposer,
    Link,
    PinRef,
    Port,
    PortRef,
    Position,
    Role,
    Supply,
    System,
    Technology,
    Tie,
    Die,
    check_interface_compatible,
    resolve_port,
)

RefLike = Union[PortRef, Tuple[str, str]]


def _as_ref(ref: RefLike) -> PortRef:
    if isinstance(ref, PortRef):
        return ref
    instance, port = ref
    return PortRef(instance=instance, port=port)


class SystemBuilder:
    """Mutable facade that accumulates a system spec and freezes it."""

    def __init__(
        self,
        name: str,
        interposer: Interposer,
        *,
        description: str = "",
        routing_layer: str = "TopMetal2",
    ) -> None:
        self.name = name
        self.interposer = interposer
        self.description = description
        self.routing_layer = routing_layer
        self._technologies: List[Technology] = []
        self._technology_ids: set = set()
        self._instances: List[DieInstance] = []
        self._instances_by_id: Dict[str, DieInstance] = {}
        self._links: List[Link] = []
        self._ties: List[Tie] = []
        self._supplies: List[Supply] = []
        self._bound_ports: set = set()
        self._methods: List[InterconnectMethod] = []
        self._method_ids: set = set()

    def add_technology(self, tech: Technology) -> None:
        # Refuse a duplicate id instead of a silent last-wins. An empty id is
        # refused at Technology ctor.
        if tech.id in self._technology_ids:
            raise DuplicateId(f"duplicate technology id {tech.id!r}")
        self._technology_ids.add(tech.id)
        self._technologies.append(tech)

    def add_method(self, method: InterconnectMethod) -> None:
        """Declare an :class:`InterconnectMethod`; refuse a duplicate id.

        No ordering constraint against ``add_technology``/``add_die``; all
        resolution happens at ``build()``. Unused declared methods are legal
        (as unused technologies are).
        """
        if method.id in self._method_ids:
            raise DuplicateId(f"duplicate interconnect method id {method.id!r}")
        self._method_ids.add(method.id)
        self._methods.append(method)

    def add_die(
        self, die: Die, id: str, position: Position, rotation_z: float = 0.0
    ) -> DieInstance:
        if id == self.interposer.id:
            raise DuplicateId(
                f"die instance id {id!r} collides with the interposer id"
            )
        if id in self._instances_by_id:
            raise DuplicateId(f"duplicate die instance id {id!r}")
        inst = DieInstance(id=id, die=die, position=position, rotation_z=rotation_z)
        self._instances.append(inst)
        self._instances_by_id[id] = inst
        return inst

    def connect(
        self, a: RefLike, b: RefLike, *, net_prefix: str
    ) -> Link:
        """Add a D2D link, validating eagerly.

        After validation the returned link's ``a`` is the MANAGER end and ``b``
        the SUBORDINATE end, whichever order the caller passed them in.
        """
        a_ref, b_ref = _as_ref(a), _as_ref(b)
        # 1. both refs resolve to an added instance and a declared port
        _, a_port = resolve_port(self._instances_by_id, a_ref)
        _, b_port = resolve_port(self._instances_by_id, b_ref)
        # 2-4. interface type, lane count, direction
        check_interface_compatible(a_ref, a_port, b_ref, b_port)
        # 5. neither port already bound
        for ref in (a_ref, b_ref):
            key = (ref.instance, ref.port)
            if key in self._bound_ports:
                raise PortAlreadyBound(
                    f"port {ref.instance}.{ref.port} is already bound by a "
                    "previous link"
                )
        # orient so a is the MANAGER end
        if a_port.role is Role.SUBORDINATE:
            a_ref, b_ref = b_ref, a_ref
        self._bound_ports.add((a_ref.instance, a_ref.port))
        self._bound_ports.add((b_ref.instance, b_ref.port))
        link = Link(a=a_ref, b=b_ref, net_prefix=net_prefix)
        self._links.append(link)
        return link

    def tie(
        self,
        instance: str,
        pin: str,
        net: str,
        *,
        net_class: str = "signal",
        layer: str = "",
    ) -> Tie:
        t = Tie(net=net, instance=instance, pin=pin, net_class=net_class, layer=layer)
        self._ties.append(t)
        return t

    def supply(
        self,
        net: str,
        pins: Iterable[Union[PinRef, Tuple[str, str]]],
        *,
        net_class: str = "power",
        layer: str = "",
    ) -> Supply:
        """Add one supply net over several pads, each given as ``(instance, pin)``.

        Checked at ``build()`` with the rest of the system: class power or
        ground, two or more endpoints, every endpoint resolved, a unique net
        name, and every pad claimed once (a pad already bound by a port lane or
        a tie is refused).
        """
        s = Supply(net=net, endpoints=tuple(pins), net_class=net_class, layer=layer)
        self._supplies.append(s)
        return s

    def build(self) -> System:
        """Freeze into a :class:`System`; re-runs every check (defense in depth)."""
        return System(
            name=self.name,
            technologies=tuple(self._technologies),
            interposer=self.interposer,
            instances=tuple(self._instances),
            links=tuple(self._links),
            ties=tuple(self._ties),
            description=self.description,
            units="um",
            routing_layer=self.routing_layer,
            methods=tuple(self._methods),
            supplies=tuple(self._supplies),
        )
