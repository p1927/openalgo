"""Fork sidecar: the optional client idempotency key on order requests.

A caller may send ``idempotency_key`` with ``placeorder`` / ``basketorder``. It is validated and
then dropped: nothing stores or enforces it yet (no uniqueness constraint until real-money
execution is planned, Trade docs/DECISIONS.md D383). Sending it now means callers already carry
the key the day the constraint lands. Absent -> behaviour is unchanged.
"""

from marshmallow import fields, post_load, validate


class IdempotencyKeyMixin:
    idempotency_key = fields.Str(
        load_default=None, validate=validate.Length(min=1, max=128), allow_none=True
    )

    @post_load
    def _drop_idempotency_key(self, data, **kwargs):
        data.pop("idempotency_key", None)
        return data
