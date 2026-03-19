"""
logger.py - 전체 시스템 공용 로깅 모듈

로그 레벨:
  DEBUG   - 상세 디버그 (개발 시)
  INFO    - 일반 진행 상황
  WARNING - 주의 필요 (데이터 누락 등)
  ERROR   - 오류 발생 (자동 복구 시도)
  CRITICAL- 치명적 오류 (시스템 중단)

로그 파일 구조:
  logs/
  ├── system/
  │   ├── trading_YYYYMMDD.log     ← 전체 이벤트
  │   └── error_YYYYMMDD.log       ← 오류만 별도
  ├── phase1/
  │   ├── collector_YYYYMMDD.log   ← 데이터 수집
  │   └── trainer_YYYYMMDD.log     ← 학습 진행
  ├── brain/
  │   └── brain_update_YYYYMMDD.log ← brain 업데이트
  └── daily/
      └── YYYYMMDD_events.log      ← 일별 매매 이벤트
"""

import logging
import os
import sys
import json
import traceback
from pathlib import Path
from datetime import datetime, date
from logging.handlers import RotatingFileHandler, TimedRotatingFileHandler
from functools import wraps
import time

# ── 기본 경로 설정 ────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LOG_DIR  = BASE_DIR / "logs"

for subdir in ["system", "phase1", "brain", "daily"]:
    (LOG_DIR / subdir).mkdir(parents=True, exist_ok=True)


# ── 컬러 포매터 (터미널 출력용) ───────────────────────────────────────────────

class ColorFormatter(logging.Formatter):
    COLORS = {
        "DEBUG":    "\033[36m",    # 청록
        "INFO":     "\033[32m",    # 초록
        "WARNING":  "\033[33m",    # 노랑
        "ERROR":    "\033[31m",    # 빨강
        "CRITICAL": "\033[35m",    # 보라
    }
    RESET = "\033[0m"
    BOLD  = "\033[1m"

    def format(self, record):
        color = self.COLORS.get(record.levelname, "")
        record.levelname = f"{color}{self.BOLD}{record.levelname:<8}{self.RESET}"
        record.msg = f"{color}{record.msg}{self.RESET}"
        return super().format(record)


# ── JSON 포매터 (파일 저장용 - 파싱 쉽게) ────────────────────────────────────

class JsonFormatter(logging.Formatter):
    def format(self, record):
        log_obj = {
            "timestamp": datetime.now().isoformat(),
            "level":     record.levelname,
            "logger":    record.name,
            "module":    record.module,
            "func":      record.funcName,
            "line":      record.lineno,
            "message":   record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        if hasattr(record, "extra"):
            log_obj["extra"] = record.extra
        return json.dumps(log_obj, ensure_ascii=False)


# ── 로거 팩토리 ───────────────────────────────────────────────────────────────

_loggers: dict[str, logging.Logger] = {}

def get_logger(
    name: str,
    log_type: str = "system",
    level: str = "INFO",
    to_console: bool = True,
    to_file: bool = True,
    json_file: bool = True,
) -> logging.Logger:
    """
    로거 생성/반환
    name:     로거 이름 (예: 'collector', 'trainer', 'brain')
    log_type: 서브디렉토리 ('system'|'phase1'|'brain'|'daily')
    level:    로그 레벨 ('DEBUG'|'INFO'|'WARNING'|'ERROR')
    """
    cache_key = f"{name}_{log_type}"
    if cache_key in _loggers:
        return _loggers[cache_key]

    logger = logging.getLogger(cache_key)
    logger.setLevel(getattr(logging, level.upper()))
    logger.handlers.clear()

    fmt_str = "%(asctime)s [%(levelname)s] %(name)s | %(message)s"
    date_fmt = "%H:%M:%S"

    # 콘솔 핸들러
    if to_console:
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.DEBUG)
        ch.setFormatter(ColorFormatter(fmt_str, datefmt=date_fmt))
        logger.addHandler(ch)

    today = date.today().strftime("%Y%m%d")

    # 텍스트 로그 파일 (읽기 쉬운 형식)
    if to_file:
        log_path = LOG_DIR / log_type / f"{name}_{today}.log"
        fh = RotatingFileHandler(
            log_path, maxBytes=10*1024*1024,  # 10MB
            backupCount=30, encoding="utf-8"
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)-8s] %(funcName)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        ))
        logger.addHandler(fh)

    # JSON 로그 파일 (프로그램 파싱용)
    if json_file:
        json_path = LOG_DIR / log_type / f"{name}_{today}.jsonl"
        jh = RotatingFileHandler(
            json_path, maxBytes=10*1024*1024,
            backupCount=30, encoding="utf-8"
        )
        jh.setLevel(logging.DEBUG)
        jh.setFormatter(JsonFormatter())
        logger.addHandler(jh)

    # 에러 전용 파일
    err_path = LOG_DIR / "system" / f"error_{today}.log"
    eh = RotatingFileHandler(
        err_path, maxBytes=5*1024*1024,
        backupCount=30, encoding="utf-8"
    )
    eh.setLevel(logging.ERROR)
    eh.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)-8s] %(name)s | %(funcName)s:%(lineno)d\n"
        "  %(message)s\n",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(eh)

    logger.propagate = False
    _loggers[cache_key] = logger
    return logger


# ── 전용 로거들 ───────────────────────────────────────────────────────────────

def get_collector_logger() -> logging.Logger:
    """데이터 수집용 로거"""
    return get_logger("collector", "phase1", level="DEBUG")

def get_trainer_logger() -> logging.Logger:
    """Phase1 학습용 로거"""
    return get_logger("trainer", "phase1", level="DEBUG")

def get_brain_logger() -> logging.Logger:
    """Brain 업데이트용 로거"""
    return get_logger("brain", "brain", level="DEBUG")

def get_trading_logger() -> logging.Logger:
    """실거래용 로거"""
    return get_logger("trading", "system", level="INFO")

def get_minority_logger() -> logging.Logger:
    """마이너리티 리포트 로거"""
    return get_logger("minority", "system", level="DEBUG")

def get_daily_logger() -> logging.Logger:
    """일별 이벤트 로거"""
    today = date.today().strftime("%Y%m%d")
    return get_logger(today, "daily", level="DEBUG")


# ── 데코레이터: 함수 실행 로깅 ───────────────────────────────────────────────

def log_call(logger=None, level="DEBUG"):
    """함수 호출/반환/오류 자동 로깅 데코레이터"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _log = logger or get_logger(func.__module__)
            _log.log(
                getattr(logging, level),
                f"▶ {func.__name__}() 시작"
                + (f" args={args[1:]}" if len(args) > 1 else "")
            )
            start = time.time()
            try:
                result = func(*args, **kwargs)
                elapsed = time.time() - start
                _log.log(
                    getattr(logging, level),
                    f"✅ {func.__name__}() 완료 ({elapsed:.2f}초)"
                )
                return result
            except Exception as e:
                elapsed = time.time() - start
                _log.error(
                    f"❌ {func.__name__}() 오류 ({elapsed:.2f}초): {e}\n"
                    f"{traceback.format_exc()}"
                )
                raise
        return wrapper
    return decorator


def log_retry(max_retries=3, delay=2.0, logger=None):
    """자동 재시도 + 로깅 데코레이터"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            _log = logger or get_logger(func.__module__)
            for attempt in range(1, max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_retries:
                        _log.error(
                            f"❌ {func.__name__}() 최대 재시도 초과 "
                            f"({max_retries}회): {e}"
                        )
                        raise
                    _log.warning(
                        f"⚠️  {func.__name__}() 재시도 {attempt}/{max_retries} "
                        f"({delay}초 후): {e}"
                    )
                    time.sleep(delay)
        return wrapper
    return decorator


# ── 진행률 표시 ───────────────────────────────────────────────────────────────

class ProgressLogger:
    """배치 작업 진행률 로깅"""

    def __init__(self, total: int, name: str, logger=None, interval: int = 10):
        self.total    = total
        self.name     = name
        self.logger   = logger or get_logger("progress")
        self.interval = interval
        self.count    = 0
        self.start    = time.time()
        self.errors   = 0

    def step(self, label: str = "", success: bool = True):
        self.count += 1
        if not success:
            self.errors += 1

        if self.count % self.interval == 0 or self.count == self.total:
            elapsed  = time.time() - self.start
            pct      = self.count / self.total * 100
            rate     = self.count / elapsed if elapsed > 0 else 0
            eta      = (self.total - self.count) / rate if rate > 0 else 0
            self.logger.info(
                f"[{self.name}] {self.count}/{self.total} "
                f"({pct:.1f}%) | "
                f"속도 {rate:.1f}건/초 | "
                f"남은시간 {eta/60:.1f}분 | "
                f"오류 {self.errors}건"
                + (f" | {label}" if label else "")
            )

    def done(self):
        elapsed = time.time() - self.start
        self.logger.info(
            f"✅ [{self.name}] 완료 | "
            f"총 {self.count}건 | "
            f"성공 {self.count - self.errors}건 | "
            f"오류 {self.errors}건 | "
            f"소요 {elapsed/60:.1f}분"
        )


# ── 로그 분석 유틸 ────────────────────────────────────────────────────────────

def tail_errors(log_type: str = "system", n: int = 20) -> list[dict]:
    """최근 오류 로그 n개 반환 (디버깅용)"""
    today    = date.today().strftime("%Y%m%d")
    jsonl    = LOG_DIR / log_type / f"error_{today}.log"
    if not jsonl.exists():
        return []
    lines = jsonl.read_text(encoding="utf-8").strip().split("\n")
    errors = []
    for line in lines[-n:]:
        try:
            errors.append(json.loads(line))
        except Exception:
            errors.append({"raw": line})
    return errors


def summarize_today(log_type: str = "phase1", name: str = "collector") -> dict:
    """오늘 로그 요약 (INFO 이상만)"""
    today  = date.today().strftime("%Y%m%d")
    jsonl  = LOG_DIR / log_type / f"{name}_{today}.jsonl"
    if not jsonl.exists():
        return {"total": 0, "errors": 0, "warnings": 0}

    counts = {"total": 0, "errors": 0, "warnings": 0, "infos": 0}
    for line in jsonl.read_text(encoding="utf-8").strip().split("\n"):
        try:
            obj = json.loads(line)
            counts["total"] += 1
            lvl = obj.get("level", "")
            if lvl == "ERROR":   counts["errors"] += 1
            if lvl == "WARNING": counts["warnings"] += 1
            if lvl == "INFO":    counts["infos"] += 1
        except Exception:
            pass
    return counts


# ── 테스트 ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    log = get_collector_logger()

    log.debug("DEBUG 메시지 - 상세 디버그")
    log.info("INFO 메시지 - 일반 진행")
    log.warning("WARNING 메시지 - 주의 필요")
    log.error("ERROR 메시지 - 오류 발생")

    # 데코레이터 테스트
    @log_call(logger=log, level="INFO")
    def sample_function(x, y):
        return x + y

    @log_retry(max_retries=3, delay=0.5, logger=log)
    def flaky_function():
        import random
        if random.random() < 0.6:
            raise ConnectionError("일시적 연결 오류")
        return "성공"

    result = sample_function(1, 2)
    log.info(f"sample_function 결과: {result}")

    # 진행률 테스트
    prog = ProgressLogger(total=50, name="테스트작업", logger=log, interval=10)
    for i in range(50):
        time.sleep(0.01)
        prog.step(f"item_{i}", success=(i % 7 != 0))
    prog.done()

    print(f"\n오늘 로그 요약: {summarize_today('phase1', 'collector')}")
    print(f"로그 디렉토리: {LOG_DIR}")
