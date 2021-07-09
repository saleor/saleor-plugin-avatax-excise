import json
import logging

import opentracing
import opentracing.tags

from saleor.celeryconf import app
from saleor.core.taxes import TaxError
from saleor.order.events import external_notification_event
from saleor.order.models import Order
from .utils import (
    AvataxConfiguration,
    api_post_request,
    get_metadata_key,
)

logger = logging.getLogger(__name__)


@app.task(
    autoretry_for=(TaxError,),
    retry_backoff=60,
    retry_kwargs={"max_retries": 5},
)
def api_post_request_task(
    transaction_url, data, config, order_id, commit_url=None
):
    config = AvataxConfiguration(**config)
    order = Order.objects.filter(id=order_id).first()
    if not order:
        msg = (
            "Unable to send the order %s to Avatax Excise. "
            "Order doesn't exist."
        )
        logger.error(msg, order_id)
        return
    if not data.get("TransactionLines", None):
        msg = (
            "The order doesn't have any line which should be "
            "sent to Avatax Excise."
        )
        external_notification_event(
            order=order, user=None, message=msg, parameters=None
        )
        return

    with opentracing.global_tracer().start_active_span(
        "avatax_excise.transactions.create"
    ) as scope:
        span = scope.span
        span.set_tag(opentracing.tags.COMPONENT, "tax")
        span.set_tag("service.name", "avatax_excise")

        tax_response = api_post_request(transaction_url, data, config)

    msg = ""
    if not tax_response:
        msg = (
            "Empty response received from Excise API, "
            f"Order: {order.token}"
        )
        logger.warning(msg)
        external_notification_event(
            order=order, user=None, message=msg, parameters=None
        )
        return
    elif tax_response.get("ReturnCode", -1) != 0:
        errors = tax_response.get("TransactionErrors", [])
        error_msg = ". ".join([
            error.get("ErrorMessage", "")
            for error in errors
        ])
        msg = f"Unable to send order to Avatax Excise. {error_msg}"
        logger.warning(
            "Unable to send order %s to Avatax Excise. Response %s",
            order.token,
            tax_response,
        )
        external_notification_event(
            order=order, user=None, message=msg, parameters=None
        )
        return
    else:
        msg = f"Order sent to Avatax Excise. Order ID: {order.token}"
        user_tran_id = tax_response.get("UserTranId")
        if config.autocommit and commit_url and user_tran_id:
            commit_url = commit_url.format(user_tran_id)
            commit_response = api_post_request(
                commit_url,
                {},
                config,
            )
            msg = f"Order committed to Avatax Excise. Order ID: {order.token}"
            if not commit_response or "Error" in commit_response.get("Status"):
                errors = commit_response.get("TransactionErrors", [])
                error_msg = ". ".join([
                    error.get("ErrorMessage", "")
                    for error in errors
                ])
                msg = f"Unable to commit order to Avatax Excise. {error_msg}"
                logger.warning(
                    "Unable to commit order %s to Avatax Excise. Response %s",
                    order.token,
                    commit_response,
                )

    tax_metadata = {
        get_metadata_key("itemized_taxes"): json.dumps(
            tax_response.get("TransactionTaxes")
        ),
        get_metadata_key("tax_transaction"): json.dumps(
            tax_response.get("Transaction")
        ),
    }
    order.store_value_in_metadata(items=tax_metadata)
    order.save()

    external_notification_event(
        order=order, user=None, message=msg, parameters=None
    )
