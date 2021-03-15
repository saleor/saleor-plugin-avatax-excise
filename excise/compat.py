from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, List, Any

if TYPE_CHECKING:
    # flake8: noqa
    from saleor.checkout.models import Checkout, CheckoutLine
    from saleor.product.models import Collection, Product, ProductVariant


@dataclass
class CheckoutLineInfo:
    line: "CheckoutLine"
    variant: "ProductVariant"
    channel_listing: Any
    product: "Product"
    collections: List["Collection"]


def fetch_checkout_lines(checkout: "Checkout") -> Iterable[CheckoutLineInfo]:
    """Fetch checkout lines as CheckoutLineInfo objects."""
    lines = checkout.lines.prefetch_related(
        "variant__product__collections", "variant__product__product_type",
    )
    lines_info = []

    for line in lines:
        variant = line.variant
        product = variant.product
        collections = list(product.collections.all())

        variant_channel_listing = None

        lines_info.append(
            CheckoutLineInfo(
                line=line,
                variant=variant,
                channel_listing=variant_channel_listing,
                product=product,
                collections=collections,
            )
        )
    return lines_info
