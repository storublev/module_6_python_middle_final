"""Перенос событий: порядок вставки и подтверждения, поведение при сбоях."""

from uuid import UUID

from tests.unit.fakes import FakeSink, FakeSource


def test_batch_is_inserted_and_committed(make_pipeline, make_event) -> None:
    """Пачка вставляется в хранилище, после чего подтверждаются смещения."""
    source = FakeSource([[make_event(), make_event('page_view')]])
    sink = FakeSink()

    pipeline = make_pipeline(source, sink)
    pipeline.run()

    assert len(sink.rows) == 2
    assert source.commits == 1
    assert pipeline.stats.rows == 2


def test_offsets_are_not_committed_without_insertion(make_pipeline, make_event) -> None:
    """Пока пачка не вставлена, смещения не подтверждаются: события не должны пропасть."""
    source = FakeSource([[make_event()]])
    # Хранилище недоступно всё время, поэтому перенос останавливают снаружи —
    # так же, как это сделал бы оркестратор сигналом.
    sink = FakeSink(failures=100)

    pipeline = make_pipeline(source, sink, breaker_failures=1, stop_after_sleeps=5)
    pipeline.run()

    assert source.commits == 0
    assert sink.rows == []


def test_temporary_failure_is_retried(make_pipeline, make_event) -> None:
    """Короткий сбой хранилища переживается повторами, пачка всё равно доезжает."""
    source = FakeSource([[make_event()]])
    sink = FakeSink(failures=2)

    pipeline = make_pipeline(source, sink)
    pipeline.run()

    assert len(sink.rows) == 1
    assert sink.inserts == 3
    assert source.commits == 1


def test_long_outage_opens_the_breaker_and_then_recovers(make_pipeline, make_event) -> None:
    """Затяжной сбой размыкает прерыватель, но после восстановления пачка доезжает."""
    source = FakeSource([[make_event()]])
    # Три отказа подряд: повторы (3 попытки) исчерпаются, прерыватель
    # разомкнётся, и следующая попытка пройдёт уже после паузы.
    sink = FakeSink(failures=3)

    pipeline = make_pipeline(source, sink, breaker_failures=1)
    pipeline.run()

    assert len(sink.rows) == 1
    assert source.commits == 1


def test_empty_batch_is_not_committed(make_pipeline) -> None:
    """Пустая пачка — это затишье, а не работа: ни вставки, ни подтверждения."""
    source = FakeSource([[]])
    sink = FakeSink()

    pipeline = make_pipeline(source, sink)
    pipeline.run()

    assert sink.inserts == 0
    assert source.commits == 0


def test_batch_of_unusable_events_is_committed(make_pipeline) -> None:
    """Пачка, где все события непригодны, подтверждается: иначе ETL встал бы на ней навсегда."""
    source = FakeSource([[{'event_type': 'click'}]])
    sink = FakeSink()

    pipeline = make_pipeline(source, sink)
    pipeline.run()

    assert sink.inserts == 0
    assert source.commits == 1
    assert pipeline.stats.skipped == 1


def test_unusable_event_does_not_take_the_batch_down(make_pipeline, make_event) -> None:
    """Непригодное событие пропускается, остальные из пачки доезжают."""
    source = FakeSource([[make_event(), {'event_type': 'click'}]])
    sink = FakeSink()

    pipeline = make_pipeline(source, sink)
    pipeline.run()

    assert len(sink.rows) == 1
    assert pipeline.stats.skipped == 1


def test_failed_commit_does_not_stop_the_transfer(make_pipeline, make_event) -> None:
    """Сбой подтверждения не роняет перенос: пачка повторится, а не пропадёт."""
    source = FakeSource([[make_event()], [make_event()]], commit_fails=True)
    sink = FakeSink()

    pipeline = make_pipeline(source, sink)
    pipeline.run()

    assert len(sink.rows) == 2


def test_stop_finishes_the_current_batch(make_pipeline, make_event) -> None:
    """Остановка не рвёт текущую пачку: сначала вставка и подтверждение, потом выход."""
    source = FakeSource([[make_event()], [make_event()], [make_event()]])
    sink = FakeSink()

    pipeline = make_pipeline(source, sink)
    original = pipeline._process

    def process_and_stop(batch):
        original(batch)
        pipeline.stop()

    pipeline._process = process_and_stop
    pipeline.run()

    assert len(sink.rows) == 1
    assert source.commits == 1


def test_memory_is_measured_after_each_batch(make_pipeline, make_event, memory) -> None:
    """После каждой пачки замеряется память: непрерывный перенос не должен течь."""
    source = FakeSource([[make_event()], [make_event()]])

    make_pipeline(source, FakeSink()).run()

    assert memory.batches == 2


def test_broken_row_does_not_stop_the_transfer(make_pipeline, make_event) -> None:
    """Строку, которую хранилище не принимает, отбрасывают, а пачку подтверждают.

    Иначе перенос встал бы навсегда: вставка падает, смещения не подтверждаются,
    после перезапуска читается та же пачка.
    """
    events = [make_event() for _ in range(4)]
    broken = events[2]['event_id']
    source = FakeSource([events])
    sink = FakeSink(broken={UUID(broken)})

    pipeline = make_pipeline(source, sink)
    pipeline.run()

    assert len(sink.rows) == 3
    assert pipeline.stats.rejected == 1
    assert source.commits == 1


def test_good_rows_of_a_broken_batch_reach_the_storage(make_pipeline, make_event) -> None:
    """Из-за одной негодной строки не теряются остальные: пачка делится пополам."""
    events = [make_event() for _ in range(8)]
    source = FakeSource([events])
    sink = FakeSink(broken={UUID(events[0]['event_id'])})

    make_pipeline(source, sink).run()

    delivered = {str(row[0]) for row in sink.rows}
    assert delivered == {event['event_id'] for event in events[1:]}


def test_data_error_does_not_open_the_breaker(make_pipeline, make_event) -> None:
    """Негодные данные — не сбой хранилища: прерыватель на них не реагирует."""
    events = [make_event(), make_event()]
    source = FakeSource([events])
    sink = FakeSink(broken={UUID(events[0]['event_id'])})

    pipeline = make_pipeline(source, sink, breaker_failures=1)
    pipeline.run()

    assert not pipeline._breaker.is_open
    assert source.commits == 1
