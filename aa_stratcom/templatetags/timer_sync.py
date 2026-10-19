"""Expose replica mode without changing the host's context-processor settings."""

from django import template

from ..sync_policy import read_only

register = template.Library()
register.simple_tag(read_only, name="timer_sync_read_only")
