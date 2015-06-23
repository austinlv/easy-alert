import os
import glob
import json
from datetime import datetime
from collections import defaultdict
from logging import WARN, ERROR

from watcher import Watcher
from easy_alert.entity import Alert, Level
from easy_alert.util import get_server_id
from easy_alert.i18n import *


class LogWatcher(Watcher):
    """
    Watch the log files filtered by Fluentd (td-agent)

    The settings of td-agent.conf should be like this.

    # Watch the log file
    <source>
      type tail
      path /var/log/messages
      pos_file /var/log/td-agent/tail.syslog.pos
      tag monitor.syslog
      format none
    </source>

    # Rewrite the tag for each line with its severity
    <match monitor.syslog>
      type rewrite_tag_filter
      rewriterule100 message ERROR ${tag}.error
      rewriterule500 message WARN ${tag}.warn
      rewriterule999 message .* clear
    </match>

    # Write to the temporary alert queue
    <match monitor.*.*>
      type file
      path /var/log/easy-alert/alert
      time_slice_format %Y%m%d_%H%M
      time_slice_wait 5s
      time_format %Y%m%dT%H%M%S%z
      localtime
      buffer_chunk_limit 256m
    </match>

    # Logs to be ignored
    <match clear>
      type null
    </match>
    """

    DEFAULT_TARGET_PATTERN = 'alert.????????_????_*.log'
    DEFAULT_PENDING_PATTERN = 'alert.????????_????*'
    DEFAULT_MESSAGE_THRESHOLD = 15
    DEFAULT_PENDING_THRESHOLD = 3

    def __init__(self, config, print_only, logger):
        watch_dir = config['watch_dir']
        target_pattern = os.path.join(watch_dir, config.get('target_pattern') or self.DEFAULT_TARGET_PATTERN)
        pending_pattern = os.path.join(watch_dir, config.get('pending_pattern') or self.DEFAULT_PENDING_PATTERN)

        super(LogWatcher, self).__init__(
            watch_dir=watch_dir, target_pattern=target_pattern, pending_pattern=pending_pattern,
            message_threshold=config.get('message_threshold') or self.DEFAULT_MESSAGE_THRESHOLD,
            pending_threshold=config.get('pending_threshold') or self.DEFAULT_PENDING_THRESHOLD,
            print_only=print_only,
            logger=logger,
            target_paths=None
        )

    def watch(self):
        """
        :return: list of Alert instances
        """
        start_time = datetime.now()

        # get target paths
        self.target_paths = glob.glob(self.target_pattern)
        if not self.target_paths:
            return self._check_pending(start_time)

        # parse files
        result, max_level = self._parse_files(self.target_paths)

        # make alert
        message = MSG_LOG_ALERT % {'server_id': get_server_id(), 'result': self._make_result(result)}
        return [Alert(start_time, max_level, MSG_LOG_ALERT_TITLE, message)]

    def after_success(self):
        """Delete parsed files after the notification."""

        for path in self.target_paths:
            if self.print_only:
                self.logger.info('Would remove: %s' % path)
            else:
                os.remove(path)

    def _check_pending(self, start_time):
        paths = glob.glob(self.pending_pattern)
        ret = []
        if len(paths) >= self.pending_threshold:
            mapping = {'server_id': get_server_id(), 'pattern': self.pending_pattern, 'paths': '\n'.join(paths)}
            ret.append(Alert(start_time, Level(WARN), MSG_LOG_ALERT_PENDING_TITLE, MSG_LOG_ALERT_PENDING % mapping))
        return ret

    def _parse_files(self, paths):
        d = defaultdict(lambda: (0, []))
        max_level = Level(WARN)
        for path in paths:
            for line in open(path):
                tokens = line.split('\t')
                tag = tokens[1]
                msg = json.loads(tokens[2].decode('utf-8', 'ignore'))['message']
                cnt, msgs = d[tag]
                cnt += 1
                msgs += [msg] if cnt <= self.message_threshold else []
                d[tag] = (cnt, msgs)
                if max_level == Level(WARN) and tag.endswith('.error'):
                    max_level = Level(ERROR)
        return d, max_level

    def _make_result(self, result):
        buf = []
        for k in sorted(result.keys()):
            cnt, msgs = result[k]
            buf.append(MSG_LOG_SUMMARY % {'tag': k, 'count': cnt})
            buf += msgs
            buf += [MSG_LOG_SNIP] if cnt > self.message_threshold else []
            buf.append('')
        return '\n'.join(buf)