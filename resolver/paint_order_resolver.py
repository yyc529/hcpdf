from __future__ import annotations

from model.elements import ImageElement, VectorElement


def assign_paint_order(
    vectors: list[VectorElement], images: list[ImageElement]
) -> None:
    """Synthesize a unified paint-order sequence number across vectors and images.

    PyMuPDF gives us:

    - ``get_drawings(extended=True)`` with a ``seqno`` per fill / stroke /
      clip / group entry. ``seqno`` advances through the *entire* content
      stream — vector fills / strokes get monotonically-increasing values,
      but **image draw operations also occupy seqno slots** (you see gaps
      in the vector seqno sequence where ``Do`` operators sit).
    - ``get_image_info(xrefs=True)`` with a ``number`` per image draw — the
      0-based index of that image among image draws on the page, **not**
      the stream-global seqno.

    We rebuild the global order by locating each image in the gaps of the
    vector seqno sequence, in image-draw order. The result is a single
    ``paint_seqno`` value per element that we can sort by; downstream code
    uses it to decide whether a simple-rect fill should sit above or below
    an image (paint-order rather than shape heuristics).
    """
    if not vectors and not images:
        return

    vector_seqnos = sorted(
        {vector.paint_seqno for vector in vectors if vector.paint_seqno is not None}
    )
    vector_set = set(vector_seqnos)
    max_seqno = vector_seqnos[-1] if vector_seqnos else -1

    # Build the ordered list of seqno slots not occupied by vectors. Images
    # fill these in their original ``number`` order. We extend the slot list
    # past ``max_seqno`` so we never run out for images drawn after the last
    # vector.
    images_in_order = sorted(images, key=lambda img: img.paint_seqno)
    original_slots = [int(img.paint_seqno) for img in images_in_order]
    if (
        len(set(original_slots)) == len(original_slots)
        and original_slots
        and original_slots != list(range(len(original_slots)))
    ):
        for image in images:
            image.source["paint_seqno"] = image.paint_seqno
        return
    gap_slots: list[int] = []
    candidate = 0
    while len(gap_slots) < len(images_in_order) + 2:
        if candidate not in vector_set:
            gap_slots.append(candidate)
        candidate += 1
        if candidate > max_seqno + len(images_in_order) + 4:
            break

    for image, slot in zip(images_in_order, gap_slots):
        image.paint_seqno = slot
        image.source["paint_seqno"] = slot
