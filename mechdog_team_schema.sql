-- ============================================================
-- MechDog 팀 통합 DB 스키마 (PostgreSQL 14+)
-- 취합/통합: 최현수 (Dog C, DB담당) · 2026-09-12
-- 구성: 0.공용원장 / 1.Dog A(출입인증) / 2.Dog B(대화음성) / 3.Dog C(에스코트)
-- Dog D(보안관제) 데이터 수신 후 공용 테이블(alerts, system_health) 추가 예정
-- ============================================================


-- ============================================================
-- 0. 공용 원장 테이블 (session_id 부모) — B의 "5-1" 결정사항 반영
--    결정: A안 채택 (B 권장안, A파트 부담 적음)
--    -> A파트가 방문자 감지 시 이 테이블에 1행 INSERT
--    -> B, C, D 등 나머지 전부 이 session_id를 FK로 참조
-- ============================================================

CREATE TABLE sessions (
    session_id   TEXT        PRIMARY KEY,
    visitor_id   TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  sessions            IS '전체 방문 세션 원장. A파트가 방문자 감지 시 1행 생성';
COMMENT ON COLUMN sessions.session_id IS '형식 예: sess-20260912143022-mechdog_a-f5b952d7';
COMMENT ON COLUMN sessions.visitor_id IS 'A파트가 부여한 방문자 ID. 영구식별자 여부는 A파트 확인 중 (B 문서 5-2)';


-- ============================================================
-- 1. Dog A (비전인식 출입게이트) — 여도훈
--    출처: "1. 얼굴 등록 (대시보드가 저장 → A파트가 읽음).txt"
-- ============================================================

-- (1) 등록인원
CREATE TABLE persons (
    person_id   TEXT        PRIMARY KEY,
    name        TEXT        NOT NULL,
    active      BOOLEAN     NOT NULL DEFAULT true,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  persons        IS '등록인원 (대시보드가 저장, A파트는 읽기 전용)';
COMMENT ON COLUMN persons.active IS 'false=재직 아님 -> A파트에서 등록 삭제 대상';

-- (2) 등록사진
CREATE TABLE person_photos (
    photo_id     BIGSERIAL   PRIMARY KEY,
    person_id    TEXT        NOT NULL REFERENCES persons(person_id) ON DELETE CASCADE,
    image        BYTEA       NOT NULL,
    content_type TEXT        NOT NULL,
    sha256       TEXT        NOT NULL,
    consent      BOOLEAN     NOT NULL,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE  person_photos          IS '등록사진 원본. 1인당 3장 이상 권장 (1장이면 오탐 위험)';
COMMENT ON COLUMN person_photos.sha256   IS '사진 변경 감지용 해시';
COMMENT ON COLUMN person_photos.consent  IS '생체정보 수집 동의 여부';
-- 주의: 얼굴 특징값(임베딩)은 이 공용 DB에 넣지 않음 (A파트 내부 보관, 생체정보 사본 최소화)

-- (3) 출입 판정 로그 (A파트가 발행 -> 구독자가 저장)
CREATE TABLE access_decisions (
    decision_id    BIGSERIAL   PRIMARY KEY,
    event_id       TEXT        NOT NULL UNIQUE,
    ts             TIMESTAMPTZ NOT NULL,
    node_id        TEXT        NOT NULL,
    decision       TEXT        NOT NULL,   -- 'allow' | 'deny' (2값, Pass/Non-Pass 아님)
    reasons        TEXT,                   -- 사유 코드, 쉼표로 여러 개 가능
    person_id      TEXT        REFERENCES persons(person_id),  -- 미인가 시 NULL
    match_score    REAL,                   -- 0~1 유사도
    helmet         TEXT,                   -- '착용' | '미착용' | '판정불가' (3값)
    latency_ms     INTEGER,
    snapshot_path  TEXT                    -- 차단 시에만 채움. 정상 통과는 NULL (NOT NULL 금지)
);

COMMENT ON TABLE  access_decisions               IS '출입판정로그 (A파트 발행 -> 구독자 저장)';
COMMENT ON COLUMN access_decisions.event_id       IS '이벤트 고유키, 중복 판별용';
COMMENT ON COLUMN access_decisions.decision       IS '허용값: allow, deny';
COMMENT ON COLUMN access_decisions.helmet         IS '허용값: 착용, 미착용, 판정불가';
COMMENT ON COLUMN access_decisions.snapshot_path  IS '차단 시에만 값 존재 (7일 보관). 정상 통과 시 NULL 유지 - NOT NULL 제약 걸지 말 것';

CREATE INDEX idx_access_decisions_ts ON access_decisions (ts DESC);


-- ============================================================
-- 2. Dog B (대화형 인터랙션 - STT/TTS) — 김별이
--    출처: "11_공유용_B_데이터항목.md" (원본 SQL 그대로 반영)
--    변경점: session_id를 공용 sessions 테이블에 FK로 연결 (5-1 A안 채택)
-- ============================================================

CREATE TABLE dialog_sessions (
    session_id         TEXT        PRIMARY KEY REFERENCES sessions(session_id),
    visitor_id         TEXT,
    started_at         TIMESTAMPTZ NOT NULL,
    ended_at           TIMESTAMPTZ,
    outcome            TEXT,
    destination        TEXT,
    purpose            TEXT,
    confidence         REAL,
    retry_count        SMALLINT    NOT NULL DEFAULT 0,
    escalation_reason  TEXT
);

COMMENT ON TABLE  dialog_sessions            IS 'MechDog B 대화 세션 요약 (1세션 1행)';
COMMENT ON COLUMN dialog_sessions.session_id IS 'A가 발급, sessions 테이블 참조 (5-1 A안)';
COMMENT ON COLUMN dialog_sessions.outcome    IS 'confirmed | escalated | abandoned_silent | abandoned_no_touch | abandoned_timeout | rejected';
COMMENT ON COLUMN dialog_sessions.destination IS 'reception | meeting_room_1 | safety_training_room | inbound_dock | outbound_dock | inspection_area | elevator_hall | exit_gate';
COMMENT ON COLUMN dialog_sessions.purpose    IS '방문 목적 원문. 전화번호·호칭 마스킹 후 저장';
COMMENT ON COLUMN dialog_sessions.escalation_reason IS 'dialog_timeout | dialog_failed (outcome=escalated 일 때만)';

CREATE TABLE dialog_turns (
    id             BIGSERIAL   PRIMARY KEY,
    session_id     TEXT        NOT NULL REFERENCES dialog_sessions(session_id) ON DELETE CASCADE,
    turn_no        SMALLINT    NOT NULL,
    ts             TIMESTAMPTZ NOT NULL,
    stt_raw        TEXT,
    stt_corrected  TEXT,
    candidates     JSONB,
    confidence     REAL,
    presence       TEXT,
    decision       TEXT,
    prompt_id      TEXT,
    touch_wait_ms  INTEGER,
    stt_ms         INTEGER,
    tts_ms         INTEGER,
    total_ms       INTEGER,
    UNIQUE (session_id, turn_no)
);

COMMENT ON TABLE  dialog_turns               IS 'MechDog B 발화 턴별 상세 (FR-B-802)';
COMMENT ON COLUMN dialog_turns.presence      IS 'present | absent | not_checked (초음파 확인 결과)';
COMMENT ON COLUMN dialog_turns.decision      IS 'confirm | strong_confirm | reask | disambiguate | prompt | leave';
COMMENT ON COLUMN dialog_turns.prompt_id     IS '재생한 고정 멘트 번호 (M-01 ~ M-14)';
COMMENT ON COLUMN dialog_turns.touch_wait_ms IS '안내 후 터치까지 걸린 시간(ms). 안 만졌으면 NULL';

CREATE INDEX idx_dialog_sessions_started_at ON dialog_sessions (started_at DESC);
CREATE INDEX idx_dialog_sessions_outcome    ON dialog_sessions (outcome);
-- dialog_turns는 UNIQUE(session_id, turn_no)가 인덱스를 겸함


-- ============================================================
-- 3. Dog C (목적지 유도·에스코트) — 최현수 (본인 파트)
--    escort_logs, event_logs는 기존 그대로 유지
--    (registered_faces, auth_logs는 A파트의 persons/access_decisions로
--     대체 - 중복 방지를 위해 더 이상 사용하지 않음)
-- ============================================================

CREATE TABLE escort_logs (
    id             SERIAL      PRIMARY KEY,
    robot_id       INTEGER     NOT NULL,
    destination    TEXT        NOT NULL,
    start_time     TIMESTAMPTZ,
    arrival_time   TIMESTAMPTZ,
    success        SMALLINT    NOT NULL DEFAULT 0,  -- 0=실패, 1=성공
    motion_played  TEXT
);

COMMENT ON TABLE escort_logs IS '에스코트(목적지 이동) 로그';

CREATE TABLE event_logs (
    event_id   SERIAL      PRIMARY KEY,
    robot_id   INTEGER,
    event_type TEXT        NOT NULL,
    timestamp  TIMESTAMPTZ NOT NULL DEFAULT now(),
    detail     TEXT
);

COMMENT ON TABLE  event_logs            IS '전체 파트(Dog A~D) 공용 이벤트 로그 (MQTT 브릿지로 자동 저장)';
COMMENT ON COLUMN event_logs.event_type IS '예: 이동시작, 도착완료, 장애물감지, 회피실패, 비상정지, 대기준비완료';


-- ============================================================
-- 4. Dog D (보안관제·대시보드) — 백경률
--    출처: D파트가 전달한 alert_logs 초안
--    결정: resolved 시 별도 이력 대신 "기존 행 갱신(UPDATE)" 방식 채택
--          (resolved/resolved_at 컬럼 자체가 갱신형 설계를 전제로 함)
-- ============================================================

CREATE TABLE alert_logs (
    alert_id       BIGSERIAL   PRIMARY KEY,
    msg_id         TEXT        UNIQUE,       -- 발행측이 부여, 해제 이벤트도 동일값 사용 (매칭 키)
    session_id     TEXT        REFERENCES sessions(session_id),
    robot_id       TEXT        NOT NULL,     -- 경고를 발행한 주체 (예: mechdog-04, mechdog_b)
    occurred_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    level          TEXT        NOT NULL,     -- 'WARNING' | 'ALERT'
    reason         TEXT        NOT NULL,     -- 예: unauthorized, no_helmet, no_vest, dialog_timeout, dialog_failed
    resolved       BOOLEAN     NOT NULL DEFAULT false,
    resolved_at    TIMESTAMPTZ,
    snapshot_path  TEXT
);

COMMENT ON TABLE  alert_logs          IS '전체 파트 공용 경고 기록 (A/B/D가 함께 사용)';
COMMENT ON COLUMN alert_logs.msg_id   IS '경고 발행 시 부여하는 고유키. 해제 이벤트도 같은 msg_id로 와야 UPDATE 매칭 가능';
COMMENT ON COLUMN alert_logs.robot_id IS '경고를 발행한 주체. 실제 로봇 개체(mechdog-04) 또는 발행 파트(mechdog_b) 둘 다 가능';
COMMENT ON COLUMN alert_logs.level    IS '허용값: WARNING, ALERT';
COMMENT ON COLUMN alert_logs.reason   IS '예: unauthorized, no_helmet, no_vest (A) / dialog_timeout, dialog_failed (B)';
COMMENT ON COLUMN alert_logs.resolved IS '관리자 해제 시 true로 UPDATE. WHERE msg_id = 매칭값 으로 대상 행 특정';

CREATE INDEX idx_alert_logs_occurred_at ON alert_logs (occurred_at DESC);
CREATE INDEX idx_alert_logs_resolved    ON alert_logs (resolved);


-- ============================================================
-- 5. 공용 테이블 진행상황
--    alerts  -> D의 alert_logs로 통합 확정 (레벨/사유가 범용 TEXT라
--               A의 보안경고, B의 대화이상(dialog_timeout 등) 전부 수용 가능)
--    system_health -> 아직 미수신. B가 요청했던 노드 상태 이력 테이블.
--                      A/B/D 전체에게 다시 확인 필요
-- ============================================================
-- system_health 대기 중
