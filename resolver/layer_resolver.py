from __future__ import annotations

from model.elements import ElementModel


def assign_layer_order(elements: list[ElementModel]) -> list[ElementModel]:
    for z_index, element in enumerate(elements):
        element.z_index = z_index
    return elements
