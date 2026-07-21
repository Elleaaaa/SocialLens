"""Pluggable notifications. Add new channels by subclassing Notifier."""
import requests


class Notifier:
    def notify(self, profile_name, platform, new_posts):
        raise NotImplementedError


class ConsoleNotifier(Notifier):
    def notify(self, profile_name, platform, new_posts):
        print(f"[ALERT] {profile_name} ({platform}) — {new_posts} new post(s)")


class WebhookNotifier(Notifier):
    """Generic JSON webhook (Slack/Discord/Telegram compatible)."""
    def __init__(self, url):
        self.url = url

    def notify(self, profile_name, platform, new_posts):
        if not self.url:
            return
        payload = {"text": f"📢 {profile_name} ({platform}): {new_posts} new post(s)"}
        try:
            requests.post(self.url, json=payload, timeout=10)
        except Exception as exc:
            print(f"[webhook error] {exc}")


# Extend this list to add channels
def get_notifiers(webhook_url=""):
    notifiers = [ConsoleNotifier()]
    if webhook_url:
        notifiers.append(WebhookNotifier(webhook_url))
    return notifiers