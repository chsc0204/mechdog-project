-- 이미 실행 중인 DB에 alert_logs.detail 컬럼 추가 (2026-09-27)
-- 실행: docker exec -i mechdog-postgres psql -U postgres -d mechdog < migration_20260927_alert_detail.sql
ALTER TABLE alert_logs ADD COLUMN IF NOT EXISTS detail TEXT;
COMMENT ON COLUMN alert_logs.detail IS '경고 상세 설명 (선택). 브릿지가 alert.event의 detail 필드를 저장';
