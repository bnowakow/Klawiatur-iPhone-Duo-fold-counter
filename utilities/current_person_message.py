"""Generate a message from our live stint, without fetching the public website."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import os
import sys


class PaceUnavailable(ValueError):
    pass


def message_for_pace(pace, folds, compact=False):
    pace = Decimal(str(pace))
    if not pace.is_finite() or pace < 0:
        raise PaceUnavailable('Nieprawidłowe tempo składania.')
    if not isinstance(folds, int) or isinstance(folds, bool) or folds < 0:
        raise PaceUnavailable('Brak wiarygodnej liczby złożeń obecnej osoby.')
    displayed = format(pace.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP), 'f')
    displayed = displayed.rstrip('0').rstrip('.').replace('.', ',')
    hourly = (pace * 60).quantize(Decimal('1'), rounding=ROUND_HALF_UP)
    if compact:
        return (f'Złożenia aktualnej osoby: {folds}. Tempo: {displayed} złożeń/min; '
                f'prognoza: {hourly} złożeń/h.')
    return (f'ilość złożeń przez aktualną osobę {folds}. '
            f'dotychczasowe tempo składania: {displayed} złożeń/min, '
            f'w tym tempie telefon powinien być złożony {hourly} razy w ciągu godziny')


def current_measurements(conn, stream, now=None):
    now = now or datetime.now(timezone.utc)
    row = conn.execute(
        'SELECT folding_started_at,last_counter_at,folding_folds_per_hour / 60, '
        'folds_since_detection_baseline '
        'FROM presenter_session_rates WHERE stream_url=%s AND ended_at IS NULL '
        'ORDER BY detected_at DESC LIMIT 1', (stream,)).fetchone()
    if not row or row[0] is None or row[2] is None or row[3] is None:
        raise PaceUnavailable('Brak wystarczających danych o tempie obecnej osoby.')
    latest = conn.execute(
        'SELECT observed_at,status,clothing_status,details FROM observations '
        'WHERE stream_url=%s ORDER BY observed_at DESC LIMIT 1', (stream,)).fetchone()
    if not latest:
        raise PaceUnavailable('Brak aktualnych odczytów licznika.')
    observed, status, clothing, details = latest
    if (status != 'analyzed' or details.get('presenter_attribution_review_required')
            or details.get('presenter_candidate_id')
            or clothing in ('change_pending', 'possible_handover_review_required',
                            'camera_view_unknown_review_required', 'night_break_detected')):
        raise PaceUnavailable('Trwa przerwa lub odczyty wymagają weryfikacji.')
    interval = float(details.get('polling_interval_seconds', os.getenv('INTERVAL_SECONDS', 4)))
    limit = max(60, interval * 2.5)
    if any(not 0 <= (now - stamp).total_seconds() <= limit for stamp in (observed, row[1])):
        raise PaceUnavailable('Odczyty obecnej osoby są nieaktualne.')
    return row[2], row[3]


def generate_current_message(stream=None, compact=False):
    """Shared, read-only entry point for clipboard, chat delivery and future bots."""
    import psycopg
    stream = stream or os.getenv('STREAM_URL', 'https://www.youtube.com/watch?v=vaiHWiExDXg')
    with psycopg.connect(os.getenv('DATABASE_URL', ''), connect_timeout=10) as conn:
        conn.execute('SET TRANSACTION READ ONLY')
        return message_for_pace(*current_measurements(conn, stream), compact=compact)


def main():
    try:
        message = generate_current_message()
    except PaceUnavailable as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(message)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
