"""Shared pytest defaults: keep the suite light and deterministic."""
import os

os.environ.setdefault("LAYA_ENABLED", "false")
os.environ.setdefault("GH_OPS_LLM_ENABLED", "false")
