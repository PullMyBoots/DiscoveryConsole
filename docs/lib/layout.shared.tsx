import type { BaseLayoutProps } from 'fumadocs-ui/layouts/shared';
import { type ReactNode } from 'react';

export function baseOptions(): BaseLayoutProps {
  return {
    nav: {
      title: (
        <>
          <img
            src="/coral_logo.png"
            alt="DiscoveryConsole"
            style={{ width: 28, height: 28, objectFit: 'contain' }}
          />
          <span style={{ fontWeight: 700, fontSize: 17, letterSpacing: '0.02em' }}>
            DiscoveryConsole
          </span>
        </>
      ) as ReactNode,
    },
    links: [
      {
        text: 'Blog',
        url: 'https://github.com/PullMyBoots/DiscoveryConsole',
      },
    ],
    githubUrl: 'https://github.com/PullMyBoots/DiscoveryConsole',
  };
}
