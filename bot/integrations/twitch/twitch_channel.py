from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

from twitchAPI.twitch import Twitch
from twitchAPI.type import AuthScope, SortOrder

if TYPE_CHECKING:
    from datetime import datetime

    from twitchAPI.object.api import AutoModSettings
    from twitchAPI.twitch import Twitch
    from twitchAPI.type import (
        AutoModCheckEntry,
        CustomRewardRedemptionStatus,
        PollStatus,
        PredictionStatus,
    )


class TwitchChannel:
    def __init__(
        self, twitch: Twitch, channel_id: str, scopes: Collection[AuthScope]
    ) -> None:
        self.twitch: Twitch = twitch
        self.channel_id: str = channel_id
        self.auth_scopes: set[AuthScope] = set(scopes)

    async def get_creator_goals(self):
        """Gets Creator Goal Details for the channel."""
        return self.twitch.get_creator_goals(self.channel_id)

    async def get_chat_settings(self, moderator_id: str | None = None):
        """Gets the broadcaster's chat settings."""
        return await self.twitch.get_chat_settings(self.channel_id, moderator_id)

    async def update_chat_settings(
        self,
        moderator_id: str,
        follower_mode_duration: int | None = None,
        non_moderator_chat_delay_duration: int | None = None,
        slow_mode_wait_time: int | None = None,
        *,
        emote_mode: bool | None = None,
        follower_mode: bool | None = None,
        non_moderator_chat_delay: bool | None = None,
        slow_mode: bool | None = None,
        subscriber_mode: bool | None = None,
        unique_chat_mode: bool | None = None,
    ):
        """Updates the broadcaster's chat settings."""
        return await self.twitch.update_chat_settings(
            self.channel_id,
            moderator_id,
            emote_mode,
            follower_mode,
            follower_mode_duration,
            non_moderator_chat_delay,
            non_moderator_chat_delay_duration,
            slow_mode,
            slow_mode_wait_time,
            subscriber_mode,
            unique_chat_mode,
        )

    async def create_clip(self, *, has_delay: bool = False):
        """Creates a clip programmatically."""
        return await self.twitch.create_clip(self.channel_id, has_delay)

    async def check_automod_status(self, automod_check_entries: list[AutoModCheckEntry]):
        """Determines whether a message meets the channel's AutoMod requirements."""
        return self.twitch.check_automod_status(self.channel_id, automod_check_entries)

    async def get_automod_settings(self, moderator_id: str):
        """Gets the broadcaster's AutoMod settings."""
        return await self.twitch.get_automod_settings(self.channel_id, moderator_id)

    async def update_automod_settings(
        self,
        moderator_id: str,
        settings: AutoModSettings | None = None,
        overall_level: int | None = None,
    ):
        """Updates the broadcaster's AutoMod settings."""
        return await self.twitch.update_automod_settings(
            self.channel_id, moderator_id, settings, overall_level
        )

    async def get_banned_users(
        self,
        user_id: str | None = None,
        after: str | None = None,
        first: int = 20,
        before: str | None = None,
    ):
        """Gets the broadcaster's list of banned users."""
        return self.twitch.get_banned_users(
            self.channel_id, user_id, after, first, before
        )

    async def ban_user(
        self,
        moderator_id: str,
        user_id: str,
        reason: str,
        duration: int | None = None,
    ):
        """Bans a user from the broadcaster's chat."""
        return await self.twitch.ban_user(
            self.channel_id, moderator_id, user_id, reason, duration
        )

    async def unban_user(self, moderator_id: str, user_id: str) -> bool:
        """Removes the ban or timeout on a user."""
        return await self.twitch.unban_user(self.channel_id, moderator_id, user_id)

    async def get_blocked_terms(
        self,
        moderator_id: str,
        after: str | None = None,
        first: int | None = None,
    ):
        """Gets the broadcaster's list of blocked terms."""
        return self.twitch.get_blocked_terms(self.channel_id, moderator_id, after, first)

    async def add_blocked_term(self, moderator_id: str, text: str):
        """Adds a blocked term to the broadcaster's chat."""
        return await self.twitch.add_blocked_term(self.channel_id, moderator_id, text)

    async def remove_blocked_term(self, moderator_id: str, term_id: str) -> bool:
        """Removes a blocked term from the broadcaster's chat."""
        return await self.twitch.remove_blocked_term(
            self.channel_id, moderator_id, term_id
        )

    async def get_moderators(
        self,
        user_ids: list[str] | None = None,
        first: int = 20,
        after: str | None = None,
    ):
        """Gets the broadcaster's list of moderators."""
        return self.twitch.get_moderators(self.channel_id, user_ids, first, after)

    async def get_broadcaster_subscriptions(
        self,
        user_ids: list[str] | None = None,
        after: str | None = None,
        first: int = 20,
    ):
        """Gets the broadcaster's list of subscriptions."""
        return await self.twitch.get_broadcaster_subscriptions(
            self.channel_id, user_ids, after, first
        )

    async def check_user_subscription(self, user_id: str):
        """Checks if a user is subscribed to the channel."""
        return await self.twitch.check_user_subscription(self.channel_id, user_id)

    async def get_channel_teams(self):
        """Gets the broadcaster's list of teams."""
        return await self.twitch.get_channel_teams(self.channel_id)

    async def get_channel_followers(
        self,
        user_id: str | None = None,
        first: int | None = None,
        after: str | None = None,
    ):
        """Gets the broadcaster's list of followers."""
        return await self.twitch.get_channel_followers(
            self.channel_id, user_id, first, after
        )

    async def modify_channel_information(
        self,
        game_id: str | None = None,
        broadcaster_language: str | None = None,
        title: str | None = None,
        delay: int | None = None,
    ):
        """Modifies the broadcaster's channel information."""
        return await self.twitch.modify_channel_information(
            self.channel_id, game_id, broadcaster_language, title, delay
        )

    async def get_stream_key(self):
        """Gets the broadcaster's stream key."""
        return await self.twitch.get_stream_key(self.channel_id)

    async def start_commercial(self, length: int):
        """Starts a commercial on the channel."""
        return await self.twitch.start_commercial(self.channel_id, length)

    async def get_chat_badges(self):
        """Gets the broadcaster's custom chat badges."""
        return await self.twitch.get_chat_badges(self.channel_id)

    async def get_channel_emotes(self):
        """Gets all emotes the channel created."""
        return await self.twitch.get_channel_emotes(self.channel_id)

    async def get_channel_icalendar(self):
        """Gets the channel's stream schedule as iCalendar."""
        return await self.twitch.get_channel_icalendar(self.channel_id)

    async def get_shared_chat_session(self):
        """Retrieves the active shared chat session."""
        return await self.twitch.get_shared_chat_session(self.channel_id)

    async def get_cheermotes(self):
        """Retrieves the list of available Cheermotes."""
        return await self.twitch.get_cheermotes(self.channel_id)

    async def get_hype_train_events(self, first: int = 1, cursor: str | None = None):
        """Gets the information of the most recent Hype Train."""
        return self.twitch.get_hype_train_events(self.channel_id, first, cursor)

    async def get_channel_stream_schedule(
        self,
        stream_segment_ids: list[str] | None = None,
        start_time: datetime | None = None,
        utc_offset: str | None = None,
        first: int = 20,
        after: str | None = None,
    ):
        """Gets the broadcaster's stream schedule."""
        return await self.twitch.get_channel_stream_schedule(
            self.channel_id, stream_segment_ids, start_time, utc_offset, first, after
        )

    async def update_channel_stream_schedule(
        self,
        vacation_start_time: datetime | None = None,
        vacation_end_time: datetime | None = None,
        timezone: str | None = None,
        *,
        is_vacation_enabled: bool | None = None,
    ):
        """Update the settings for a channel's stream schedule."""
        await self.twitch.update_channel_stream_schedule(
            self.channel_id,
            is_vacation_enabled,
            vacation_start_time,
            vacation_end_time,
            timezone,
        )

    async def create_channel_stream_schedule_segment(
        self,
        start_time: datetime,
        timezone: str,
        duration: str | None = None,
        category_id: str | None = None,
        title: str | None = None,
        *,
        is_recurring: bool,
    ):
        """Create a scheduled broadcast for a channel's stream schedule."""
        return await self.twitch.create_channel_stream_schedule_segment(
            self.channel_id,
            start_time,
            timezone,
            is_recurring,
            duration,
            category_id,
            title,
        )

    async def update_channel_stream_schedule_segment(
        self,
        stream_segment_id: str,
        start_time: datetime | None = None,
        duration: str | None = None,
        category_id: str | None = None,
        title: str | None = None,
        timezone: str | None = None,
        *,
        is_canceled: bool | None = None,
    ):
        """Update a scheduled broadcast for a channel's stream schedule."""
        return await self.twitch.update_channel_stream_schedule_segment(
            self.channel_id,
            stream_segment_id,
            start_time,
            duration,
            category_id,
            title,
            is_canceled,
            timezone,
        )

    async def delete_channel_stream_schedule_segment(self, stream_segment_id: str):
        """Delete a scheduled broadcast for a channel's stream schedule."""
        await self.twitch.delete_channel_stream_schedule_segment(
            self.channel_id, stream_segment_id
        )

    async def remove_channel_vip(self, user_id: str) -> bool:
        """Removes a VIP from the broadcaster's chat room."""
        return await self.twitch.remove_channel_vip(self.channel_id, user_id)

    async def add_channel_vip(self, user_id: str) -> bool:
        """Adds a VIP to the broadcaster's chat room."""
        return await self.twitch.add_channel_vip(self.channel_id, user_id)

    async def get_vips(
        self,
        user_ids: str | list[str] | None = None,
        first: int | None = None,
        after: str | None = None,
    ):
        """Gets a list of the channel's VIPs."""
        return self.twitch.get_vips(self.channel_id, user_ids, first, after)

    async def add_channel_moderator(self, user_id: str):
        """Adds a moderator to the broadcaster's chat room."""
        await self.twitch.add_channel_moderator(self.channel_id, user_id)

    async def remove_channel_moderator(self, user_id: str):
        """Removes a moderator from the broadcaster's chat room."""
        await self.twitch.remove_channel_moderator(self.channel_id, user_id)

    async def delete_chat_message(self, moderator_id: str, message_id: str | None = None):
        """Removes a chat message from the broadcaster's chat room."""
        await self.twitch.delete_chat_message(self.channel_id, moderator_id, message_id)

    async def send_chat_announcement(
        self, moderator_id: str, message: str, color: str | None = None
    ):
        """Sends an announcement to the broadcaster's chat room."""
        await self.twitch.send_chat_announcement(
            self.channel_id, moderator_id, message, color
        )

    async def get_chatters(
        self, moderator_id: str, first: int | None = None, after: str | None = None
    ):
        """Gets the list of users connected to the broadcaster's chat session."""
        return await self.twitch.get_chatters(self.channel_id, moderator_id, first, after)

    async def get_shield_mode_status(self, moderator_id: str):
        """Gets the broadcaster's Shield Mode activation status."""
        return await self.twitch.get_shield_mode_status(self.channel_id, moderator_id)

    async def update_shield_mode_status(self, moderator_id: str, *, is_active: bool):
        """Activates or deactivates the broadcaster's Shield Mode."""
        return await self.twitch.update_shield_mode_status(
            self.channel_id, moderator_id, is_active
        )

    async def get_charity_campaign(self):
        """Gets information about the charity campaign that a broadcaster is running."""
        return await self.twitch.get_charity_campaign(self.channel_id)

    async def get_charity_donations(
        self, first: int | None = None, after: str | None = None
    ):
        """Gets the list of donations to the broadcaster's active charity campaign."""
        return self.twitch.get_charity_donations(self.channel_id, first, after)

    async def get_ad_schedule(self):
        """Returns ad schedule related information."""
        return await self.twitch.get_ad_schedule(self.channel_id)

    async def snooze_next_ad(self):
        """Pushes back the timestamp of the upcoming automatic mid-roll ad."""
        return await self.twitch.snooze_next_ad(self.channel_id)

    async def send_chat_message(
        self,
        sender_id: str,
        message: str,
        reply_parent_message_id: str | None = None,
        *,
        for_source_only: bool | None = None,
    ):
        """Sends a message to the broadcaster's chat room."""
        return await self.twitch.send_chat_message(
            self.channel_id,
            sender_id,
            message,
            reply_parent_message_id,
            for_source_only,
        )

    async def warn_chat_user(self, moderator_id: str, user_id: str, reason: str):
        """Warns a user in the broadcaster's chat room."""
        return await self.twitch.warn_chat_user(
            self.channel_id, moderator_id, user_id, reason
        )

    async def get_clips_download(self, editor_id: str, clip_ids: list[str]):
        """Provides URLs to download the video file(s) for the specified clips."""
        return await self.twitch.get_clips_download(editor_id, self.channel_id, clip_ids)

    async def create_custom_reward(
        self,
        title: str,
        cost: int,
        prompt: str | None = None,
        background_color: str | None = None,
        max_per_stream: int | None = None,
        max_per_user_per_stream: int | None = None,
        global_cooldown_seconds: int | None = None,
        *,
        is_enabled: bool = True,
        is_user_input_required: bool = False,
        is_max_per_stream_enabled: bool = False,
        is_max_per_user_per_stream_enabled: bool = False,
        is_global_cooldown_enabled: bool = False,
        should_redemptions_skip_request_queue: bool = False,
    ):
        """Creates a Custom Reward on a channel."""
        return await self.twitch.create_custom_reward(
            self.channel_id,
            title,
            cost,
            prompt,
            is_enabled,
            background_color,
            is_user_input_required,
            is_max_per_stream_enabled,
            max_per_stream,
            is_max_per_user_per_stream_enabled,
            max_per_user_per_stream,
            is_global_cooldown_enabled,
            global_cooldown_seconds,
            should_redemptions_skip_request_queue,
        )

    async def delete_custom_reward(self, reward_id: str):
        """Deletes a Custom Reward on a channel."""
        await self.twitch.delete_custom_reward(self.channel_id, reward_id)

    async def get_custom_reward(
        self,
        reward_id: str | list[str] | None = None,
        *,
        only_manageable_rewards: bool = False,
    ):
        """Returns a list of Custom Reward objects for the channel."""
        return await self.twitch.get_custom_reward(
            self.channel_id, reward_id, only_manageable_rewards
        )

    async def get_custom_reward_redemption(
        self,
        reward_id: str,
        redemption_id: list[str] | None = None,
        status: CustomRewardRedemptionStatus | None = None,
        sort: SortOrder = SortOrder.OLDEST,
        after: str | None = None,
        first: int = 20,
    ):
        """Returns Custom Reward Redemption objects for a Custom Reward."""
        return self.twitch.get_custom_reward_redemption(
            self.channel_id, reward_id, redemption_id, status, sort, after, first
        )

    async def update_custom_reward(
        self,
        reward_id: str,
        title: str | None = None,
        prompt: str | None = None,
        cost: int | None = None,
        background_color: str | None = None,
        max_per_stream: int | None = None,
        max_per_user_per_stream: int | None = None,
        global_cooldown_seconds: int | None = None,
        *,
        is_enabled: bool = True,
        is_user_input_required: bool = False,
        is_max_per_stream_enabled: bool = False,
        is_max_per_user_per_stream_enabled: bool = False,
        is_global_cooldown_enabled: bool = False,
        is_paused: bool = False,
        should_redemptions_skip_request_queue: bool = False,
    ):
        """Updates a Custom Reward created on a channel."""
        return await self.twitch.update_custom_reward(
            self.channel_id,
            reward_id,
            title,
            prompt,
            cost,
            is_enabled,
            background_color,
            is_user_input_required,
            is_max_per_stream_enabled,
            max_per_stream,
            is_max_per_user_per_stream_enabled,
            max_per_user_per_stream,
            is_global_cooldown_enabled,
            global_cooldown_seconds,
            is_paused,
            should_redemptions_skip_request_queue,
        )

    async def update_redemption_status(
        self,
        reward_id: str,
        redemption_ids: list[str] | str,
        status: CustomRewardRedemptionStatus,
    ):
        """Updates the status of Custom Reward Redemption objects."""
        return await self.twitch.update_redemption_status(
            self.channel_id, reward_id, redemption_ids, status
        )

    async def get_channel_editors(self):
        """Gets a list of users who have editor permissions for the channel."""
        return await self.twitch.get_channel_editors(self.channel_id)

    async def get_user_block_list(self, first: int = 20, after: str | None = None):
        """Gets the broadcaster's block list."""
        return self.twitch.get_user_block_list(self.channel_id, first, after)

    async def get_polls(
        self,
        poll_id: str | list[str] | None = None,
        after: str | None = None,
        first: int = 20,
    ):
        """Get information about all polls or specific polls for the channel."""
        return self.twitch.get_polls(self.channel_id, poll_id, after, first)

    async def create_poll(
        self,
        title: str,
        choices: list[str],
        duration: int,
        channel_points_per_vote: int | None = None,
        *,
        channel_points_voting_enabled: bool = False,
    ):
        """Create a poll for the channel."""
        return await self.twitch.create_poll(
            self.channel_id,
            title,
            choices,
            duration,
            channel_points_voting_enabled,
            channel_points_per_vote,
        )

    async def end_poll(self, poll_id: str, status: PollStatus):
        """End a poll that is currently active."""
        return await self.twitch.end_poll(self.channel_id, poll_id, status)

    async def get_predictions(
        self,
        prediction_ids: list[str] | None = None,
        after: str | None = None,
        first: int = 20,
    ):
        """Get information about all Channel Points Predictions for the channel."""
        return self.twitch.get_predictions(self.channel_id, prediction_ids, after, first)

    async def create_prediction(
        self, title: str, outcomes: list[str], prediction_window: int
    ):
        """Create a Channel Points Prediction for the channel."""
        return await self.twitch.create_prediction(
            self.channel_id, title, outcomes, prediction_window
        )

    async def end_prediction(
        self,
        prediction_id: str,
        status: PredictionStatus,
        winning_outcome_id: str | None = None,
    ):
        """Lock, resolve, or cancel a Channel Points Prediction."""
        return await self.twitch.end_prediction(
            self.channel_id, prediction_id, status, winning_outcome_id
        )

    async def cancel_raid(self):
        """Cancel a pending raid."""
        await self.twitch.cancel_raid(self.channel_id)
