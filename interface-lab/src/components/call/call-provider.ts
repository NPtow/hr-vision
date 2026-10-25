export type CallProvider = 'daily' | 'zoom';
export type CallProviderOption = { id: CallProvider; available: boolean };
export type CallProviderMeeting = {
  callProvider?: CallProvider;
  callProviderLocked?: boolean;
  callProviderOptions?: CallProviderOption[];
  videoConfigured?: boolean;
};
export type DailyCredentials = { provider?: 'daily'; url: string; token: string };
export type ZoomCredentials = {
  provider: 'zoom'; meetingNumber: string; passWord: string; userName: string;
  role: 0 | 1; signature: string; zak?: string; sdkVersion: '6.10.0';
};
export type CallCredentials = DailyCredentials | ZoomCredentials;
