import { useEffect } from 'react';

const useTabCloseEffect = () => {
  useEffect(() => {
    if (
      import.meta.env.REACT_APP_LOCAL?.toLowerCase() === 'true'
    ) {
      return;
    }

    const notificationNodeUrl =
      import.meta.env.REACT_APP_NOTIFICATION_NODE_URL;

    if (!notificationNodeUrl) {
      console.warn(
        'REACT_APP_NOTIFICATION_NODE_URL is not configured'
      );
      return;
    }

    const timeout = window.setTimeout(() => {
      const url = `${notificationNodeUrl.replace(/\/$/, '')}/remove-from-logout-queue`;

      if (navigator.sendBeacon) {
        const body = new Blob([], {
          type: 'application/x-www-form-urlencoded',
        });

        navigator.sendBeacon(url, body);
        return;
      }

      fetch(url, {
        method: 'POST',
        credentials: 'include',
        keepalive: true,
      }).catch((error) => {
        console.error(
          'Failed to cancel pending logout:',
          error
        );
      });
    }, 2500);

    return () => window.clearTimeout(timeout);
  }, []);
};

export default useTabCloseEffect;
