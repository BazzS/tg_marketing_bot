from __future__ import annotations

from abc import ABC, abstractmethod

from .models import Ad, Campaign, MetricSnapshot


class AdPlatformClient(ABC):
    """Abstract interface for an ad platform."""

    @abstractmethod
    def create_campaign(self, campaign: Campaign) -> Campaign: ...

    @abstractmethod
    def update_campaign(self, campaign: Campaign) -> Campaign: ...

    @abstractmethod
    def pause_campaign(self, campaign_id: str) -> None: ...

    @abstractmethod
    def resume_campaign(self, campaign_id: str) -> None: ...

    @abstractmethod
    def create_ad(self, ad: Ad) -> Ad: ...

    @abstractmethod
    def pause_ad(self, ad_id: str) -> None: ...

    @abstractmethod
    def fetch_metrics(self, campaign_id: str) -> MetricSnapshot:
        """Fetch metrics for the current period (one cycle)."""
        ...

    @abstractmethod
    def fetch_ad_metrics(self, ad_id: str, campaign_id: str) -> MetricSnapshot: ...
