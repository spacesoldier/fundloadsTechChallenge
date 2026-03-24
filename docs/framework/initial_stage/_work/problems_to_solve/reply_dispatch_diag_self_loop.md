# Bug: Self-loop RoutingError for `ControlPlaneLeafReplyDispatchDiagEvent`

**Обнаружено:** 2026-03-23, сессия `20260323T205324Z`
**Статус:** ❌ не исправлено
**Фаза плана:** Phase 1 (H-1 handshake diagnostics)
**Приоритет:** Блокирующий — вызывает crash obs-воркера и срыв shutdown

---

## Контекст

В рамках Phase 1 плана `performance_and_shutdown_recovery_plan_2026-03-23.md` был добавлен
диагностический тип `ControlPlaneLeafReplyDispatchDiagEvent` — для трассировки того,
принимается ли `DiscoveryAck`/`ConfigAck` в `leaf_reply_dispatch` и доходит ли до root.

---

## Симптом (из лога сессии)

```
Process sk:system.observability#1:
Traceback (most recent call last):
  File ".../runner.py", line 1538, in run_async
    routing_result = router.route([output], source=node_name)
  ...
stream_kernel.routing.errors.RoutingError:
    Self-loop for 'ControlPlaneLeafReplyDispatchDiagEvent' requires explicit target
```

Процесс `system.observability#1` упал немедленно после `leaf discovery acknowledged`.
Остальные воркеры (бизнес-группы) продолжили работу — все 1000 записей обработаны
(`output=1000`), но shutdown не завершился в отведённые 60 с (`rc=124`).

---

## Позитивный результат прогона

Несмотря на падение:

| Метрика | Значение |
|---|---|
| `output` | **1000** — все записи обработаны ✓ |
| `trace_all` | 10025 |
| `trace_biz` | 10025 |
| `rc` | 124 (timeout, не crash бизнес-воркеров) |
| Оставшиеся процессы | `2826066–2826069` (бизнес-воркеры, не obs) |

Это означает что **H-1 для бизнес-воркеров исправлен или обойдён** — они успешно
обрабатывают данные. Obs-воркер упал, но на business throughput не повлиял.

---

## Root cause

### Цепочка событий

1. `system.observability#1` завершает `DiscoveryAck`
2. `system.cp.leaf_snapshot_apply` обрабатывает `ControlPlaneLeafDiscoverySnapshotEvent`
   и **эмитирует оба**:
   ```python
   return [
       ControlPlaneLeafDiscoveryAckEvent(...),         # → leaf_reply_dispatch
       ControlPlaneLeafReplyDispatchDiagEvent(...),    # → leaf_reply_dispatch
   ]
   ```
3. `system.cp.leaf_reply_dispatch` обрабатывает `ControlPlaneLeafDiscoveryAckEvent`:
   - Вызывает `dispatch_reply(worker_id, DiscoveryAckEvent)` → отправляет в root по IPC ✓
   - Вызывает `_leaf_reply_dispatch_diag_for_payload(payload=DiscoveryAckEvent, accepted=True)`
   - Возвращает `[ControlPlaneLeafReplyDispatchDiagEvent(stage="leaf_reply_dispatch", ...)]`
4. Router пытается доставить это событие:
   - **Источник:** `system.cp.leaf_reply_dispatch`
   - **Consumer из routing table:** `system.cp.leaf_reply_dispatch`
   - Это **self-loop** без explicit target → `RoutingError`

### Схема конфликта

```
leaf_snapshot_apply ──emits──► ControlPlaneLeafDiscoveryAckEvent
                                        │
                                        ▼
                            system.cp.leaf_reply_dispatch
                                        │
                                    processes
                                        │
                                        ▼
                            emits ControlPlaneLeafReplyDispatchDiagEvent
                                        │
                         routing table says: → leaf_reply_dispatch
                                        │
                                        ▼
                            ⚡ SELF-LOOP → RoutingError ⚡
```

### Почему только obs, а не бизнес-воркеры

Бизнес-воркеры не дошли до шага отправки `DiscoveryAck` (либо H-1 ещё не полностью
исправлен для них, либо они обрабатывают данные через fallback без завершения handshake).
`ControlPlaneLeafReplyDispatchDiagEvent` генерируется именно при обработке `DiscoveryAck` /
`ConfigAck` в `leaf_reply_dispatch`. Obs — единственный воркер, который дошёл до
`DiscoveryAck` и сразу упал.

---

## Детали routing-таблицы (leaf/plan_builder.py)

```python
# Текущее состояние — проблемная запись:
ControlPlaneLeafReplyDispatchDiagEvent: ["system.cp.leaf_reply_dispatch"],
```

Нода `leaf_reply_dispatch`:

```python
@node(
    name="system.cp.leaf_reply_dispatch",
    consumes=[..., ControlPlaneLeafReplyDispatchDiagEvent, ...],
    emits=[ControlPlaneLeafSinkDispatchAckEvent, ControlPlaneLeafReplyDispatchDiagEvent],
)
```

Нода одновременно **потребляет** и **эмитирует** один и тот же тип без explicit target —
это запрещено роутером.

---

## Варианты исправления

### Вариант A — Диагностику dispatch inline через уже инжектированный сервис (рекомендуется)

`ControlPlaneLeafReplyDispatchNode` уже имеет
`handoff_dispatch: ExecutionIpcHandoffDispatchService = inject.service(ExecutionIpcHandoffDispatchService)`
и уже вызывает `self.handoff_dispatch.dispatch_reply()` для основных событий (`HelloEvent`,
`DiscoveryAckEvent` и т.д.) — это стандартный паттерн этой ноды. Вариант A не добавляет
новых зависимостей: только перемещает diag из return value в прямой вызов уже
инжектированного сервиса:

```python
diag = _leaf_reply_dispatch_diag_for_payload(payload=payload, accepted=accepted)
if diag is not None:
    self.handoff_dispatch.dispatch_reply(worker_id=worker_id, payload=diag)
# return [] или [ack] — без diag в outputs
```

Это полностью соответствует платформенной философии: сервисы вызываются через
`inject.service()`, нода не производит побочных эффектов вне контракта. Нода
перестаёт эмитировать `ControlPlaneLeafReplyDispatchDiagEvent` через граф, self-loop
невозможен. Root получает диагностику по тому же IPC-каналу.

### Вариант B — Explicit target на envelope

Возвращать diag-событие завёрнутым в `Envelope(target="system.cp.leaf_reply_dispatch")`.
Router принимает self-loop с явным target. Но это архитектурно грязно —
нода явно адресует себя, что нарушает граф-семантику.

### Вариант C — Удалить `ControlPlaneLeafReplyDispatchDiagEvent` из emits ноды

Убрать это из `emits` в декораторе `@node`. Тогда router не будет знать о том,
что нода эмитирует этот тип, и при routing поведение зависит от конфигурации
`allow_unknown_emits`. Ненадёжно.

### Вариант D — Выделить отдельную diag-sink ноду

```python
# routing table:
ControlPlaneLeafReplyDispatchDiagEvent: ["system.cp.leaf_diag_sink"],
```

Новая нода `system.cp.leaf_diag_sink` принимает `ControlPlaneLeafReplyDispatchDiagEvent`
и диспатчит их в root. Чисто, но добавляет новую ноду ради одного типа события.

---

## Рекомендация

**Вариант A** — самый простой и правильный. `leaf_reply_dispatch` уже имеет доступ
к `handoff_dispatch`. Диагностическое событие по природе своей должно быть отправлено
туда же куда идут основные события (в root), а не циркулировать по локальному графу.

Изменение минимально и не нарушает платформенную философию:
`handoff_dispatch` уже инжектирован в ноду через `inject.service()`, нода уже
вызывает его напрямую для всех остальных событий — паттерн идентичен.

Конкретно: в `__call__` убрать `diag` из return value и вызвать
`self.handoff_dispatch.dispatch_reply(worker_id=worker_id, payload=diag)` напрямую.
Из `@node(..., emits=[...])` убрать `ControlPlaneLeafReplyDispatchDiagEvent`.
Из routing table в `leaf/plan_builder.py` тоже убрать эту строку —
нода больше не эмитирует этот тип через граф, он уходит прямо в IPC.

---

## Что это означает для плана

| Аспект | Состояние |
|---|---|
| H-1 (business handshake) | ⚠️ Частично — `output=1000`, но DiscoveryAck для бизнес-воркеров в логе не виден. Требует подтверждения. |
| Obs-воркер handshake | ❌ Падает на DiscoveryAck из-за self-loop |
| Data throughput | ✅ 1000/1000 записей обработано |
| Shutdown completion | ❌ rc=124, timeout — obs упал до DrainReady, shutdown chain не завершился |
| Self-loop bug | ❌ Не исправлено |

**Следующий шаг по плану:** исправить Вариант A (не менять платформу — только
`leaf/system_nodes.py` в части метода `__call__` `ControlPlaneLeafReplyDispatchNode`
и декоратора), перезапустить, убедиться что obs завершает handshake без краша,
и проверить достигается ли shutdown.
