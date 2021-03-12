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
        # for channel_listing in Vline.variant.channel_listings.all():
        #     if channel_listing.channel_id == checkout.channel_id:
        #         variant_channel_listing = channel_listing

        # FIXME: Temporary solution to pass type checks. Figure out how to handle case
        # when variant channel listing is not defined for a checkout line.
        # if not variant_channel_listing:
        #     continue

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
