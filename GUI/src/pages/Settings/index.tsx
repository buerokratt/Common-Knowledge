import { FC, useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery, useMutation } from '@tanstack/react-query';
import { MdOutlineSchedule } from 'react-icons/md';
import {
  Button,
  FormInput,
  FormSelect,
  SwitchBox,
  Icon,
  Track,
  Card,
} from 'components';
import { useToast } from 'hooks/useToast';
import {
  updateSourceScrapeInterval,
  getSource,
  Source,
} from 'services/sources';
import { apiDev } from 'services/api';
import './Settings.scss';

interface UpdateSettings {
  updateAutomatically: boolean;
  repeatUnit: 'days' | 'weeks' | 'months' | 'years';
  daysOfWeek?: string[];
  dayOfMonth?: number;
  monthOfYear?: string;
  weekPosition?: 'first' | 'second' | 'third' | 'fourth' | 'last';
  dayOfWeek?: string;
  monthsIntervalDayOfMonth?: number;
  monthsIntervalWeekPosition?: number;
  monthlyType?: 'dayOfMonth' | 'weekPosition';
  yearlyType?: 'dayOfMonth' | 'weekPosition';
  timeOfUpdate: string;
}

const SourceSettings: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const navigate = useNavigate();
  const { id: sourceId } = useParams<{ id: string }>();
  const [settings, setSettings] = useState<UpdateSettings>({
    updateAutomatically: false,
    repeatUnit: 'days',
    timeOfUpdate: '00:00',
    monthlyType: 'dayOfMonth',
    yearlyType: 'dayOfMonth',
    monthsIntervalDayOfMonth: 2,
    monthsIntervalWeekPosition: 2,
    dayOfMonth: 2,
    weekPosition: 'last',
    dayOfWeek: 'wednesday',
    monthOfYear: 'December',
    daysOfWeek: ['Mon'],
  });

  const [isInitialized, setIsInitialized] = useState(false);

  // Fetch source data
  const { data: sourceData, isLoading } = useQuery({
    queryKey: ['source', sourceId],
    queryFn: () => getSource(sourceId!),
    enabled: !!sourceId,
    // Remove onSuccess to prevent state overwrites
  });

  // Parse cron expression function
  const parseCronExpression = (
    cronExpression: string
  ): Partial<UpdateSettings> => {
    if (!cronExpression || cronExpression.trim() === '') {
      return {};
    }

    const parts = cronExpression.trim().split(' ');
    if (parts.length !== 5) {
      console.warn('Invalid cron expression format:', cronExpression);
      return {};
    }

    const [minutes, hours, dayOfMonth, month, dayOfWeek] = parts;

    const settings: Partial<UpdateSettings> = {
      timeOfUpdate: `${hours.padStart(2, '0')}:${minutes.padStart(2, '0')}`,
    };

    // Daily schedule: * * * * *
    if (dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
      settings.repeatUnit = 'days';
      return settings;
    }

    // Weekly schedule: * * * * 0-6
    if (dayOfMonth === '*' && month === '*' && dayOfWeek !== '*') {
      settings.repeatUnit = 'weeks';

      // Handle single day
      if (!dayOfWeek.includes(',') && !dayOfWeek.includes('-')) {
        const dayMapping: { [key: string]: string } = {
          '0': 'Sun',
          '1': 'Mon',
          '2': 'Tue',
          '3': 'Wed',
          '4': 'Thu',
          '5': 'Fri',
          '6': 'Sat',
        };
        settings.daysOfWeek = [dayMapping[dayOfWeek] || 'Sun'];
      }
      return settings;
    }

    // Monthly schedule
    if (month === '*' || month.includes('/')) {
      settings.repeatUnit = 'months';

      // Parse month interval
      const monthInterval = month.includes('/')
        ? parseInt(month.split('/')[1])
        : 1;

      // Check if it's a week position pattern (e.g., 1#1, 2#2, etc. or 1L, 2L, etc.)
      if (dayOfWeek.includes('#') || dayOfWeek.includes('L')) {
        settings.monthlyType = 'weekPosition';
        settings.monthsIntervalWeekPosition = monthInterval;

        const dayMapping: { [key: string]: string } = {
          '1': 'monday',
          '2': 'tuesday',
          '3': 'wednesday',
          '4': 'thursday',
          '5': 'friday',
          '6': 'saturday',
          '0': 'sunday',
        };

        if (dayOfWeek.includes('L')) {
          // Last occurrence pattern
          const day = dayOfWeek.replace('L', '');
          settings.weekPosition = 'last';
          settings.dayOfWeek = dayMapping[day] || 'wednesday';
        } else if (dayOfWeek.includes('#')) {
          // Specific occurrence pattern
          const [day, occurrence] = dayOfWeek.split('#');
          const positionMapping: {
            [key: string]: 'first' | 'second' | 'third' | 'fourth';
          } = {
            '1': 'first',
            '2': 'second',
            '3': 'third',
            '4': 'fourth',
          };
          settings.weekPosition = positionMapping[occurrence] || 'first';
          settings.dayOfWeek = dayMapping[day] || 'wednesday';
        }
      } else {
        // Day of month pattern
        settings.monthlyType = 'dayOfMonth';
        settings.dayOfMonth = parseInt(dayOfMonth) || 1;
        settings.monthsIntervalDayOfMonth = monthInterval;
      }
      return settings;
    }

    // Yearly schedule
    if (month !== '*' && !month.includes('/')) {
      settings.repeatUnit = 'years';

      const monthMapping: { [key: string]: string } = {
        '1': 'January',
        '2': 'February',
        '3': 'March',
        '4': 'April',
        '5': 'May',
        '6': 'June',
        '7': 'July',
        '8': 'August',
        '9': 'September',
        '10': 'October',
        '11': 'November',
        '12': 'December',
      };

      settings.monthOfYear = monthMapping[month] || 'December';

      // Check if it's a week position pattern
      if (dayOfWeek.includes('#') || dayOfWeek.includes('L')) {
        settings.yearlyType = 'weekPosition';

        const dayMapping: { [key: string]: string } = {
          '1': 'monday',
          '2': 'tuesday',
          '3': 'wednesday',
          '4': 'thursday',
          '5': 'friday',
          '6': 'saturday',
          '0': 'sunday',
        };

        if (dayOfWeek.includes('L')) {
          const day = dayOfWeek.replace('L', '');
          settings.weekPosition = 'last';
          settings.dayOfWeek = dayMapping[day] || 'wednesday';
        } else if (dayOfWeek.includes('#')) {
          const [day, occurrence] = dayOfWeek.split('#');
          const positionMapping: {
            [key: string]: 'first' | 'second' | 'third' | 'fourth';
          } = {
            '1': 'first',
            '2': 'second',
            '3': 'third',
            '4': 'fourth',
          };
          settings.weekPosition = positionMapping[occurrence] || 'first';
          settings.dayOfWeek = dayMapping[day] || 'wednesday';
        }
      } else {
        // Day of month pattern
        settings.yearlyType = 'dayOfMonth';
        settings.dayOfMonth = parseInt(dayOfMonth) || 1;
      }
      return settings;
    }

    return settings;
  };

  // Initialize settings only once when data is loaded
  useEffect(() => {
    if (sourceData && !isInitialized) {
      const parsedSettings = sourceData.cronSchedule
        ? parseCronExpression(sourceData.cronSchedule)
        : {};

      setSettings((prev) => ({
        ...prev,
        url: sourceData.url,
        updateAutomatically: sourceData.updateAutomatically || false,
        ...parsedSettings,
      }));

      setIsInitialized(true);
    }
  }, [sourceData, isInitialized]);

  // Update scrape interval mutation
  const updateMutation = useMutation({
    mutationFn: ({
      baseId,
      cronSchedule,
      updateAutomatically,
    }: {
      baseId: string;
      cronSchedule: string;
      updateAutomatically: boolean;
    }) => updateSourceScrapeInterval(baseId, cronSchedule, updateAutomatically),
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.settingsUpdated'),
      });
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.settingsUpdateError'),
      });
    },
  });

  // Rest of your component remains the same...
  const generateCronExpression = (): string => {
    const timeValue = settings.timeOfUpdate || '0:0';
    const [hours, minutes] = timeValue.includes(':')
      ? timeValue.split(':')
      : ['0', '0'];
    const cronMinutes = minutes || '0';
    const cronHours = hours || '0';

    switch (settings.repeatUnit) {
      case 'days':
        return `${cronMinutes} ${cronHours} * * *`;

      case 'weeks':
        if (settings.daysOfWeek && settings.daysOfWeek.length > 0) {
          const dayMapping: { [key: string]: string } = {
            Sun: '0',
            Mon: '1',
            Tue: '2',
            Wed: '3',
            Thu: '4',
            Fri: '5',
            Sat: '6',
          };
          const cronDay = dayMapping[settings.daysOfWeek[0]];
          return `${cronMinutes} ${cronHours} * * ${cronDay}`;
        }
        return `${cronMinutes} ${cronHours} * * 0`;

      case 'months':
        if (
          settings.monthlyType === 'weekPosition' &&
          settings.weekPosition &&
          settings.dayOfWeek
        ) {
          const dayMapping: { [key: string]: string } = {
            monday: '1',
            tuesday: '2',
            wednesday: '3',
            thursday: '4',
            friday: '5',
            saturday: '6',
            sunday: '0',
          };
          const cronDay = dayMapping[settings.dayOfWeek];
          const monthInterval = settings.monthsIntervalWeekPosition || 1;

          if (settings.weekPosition === 'last') {
            if (monthInterval === 1) {
              return `${cronMinutes} ${cronHours} * * ${cronDay}L`;
            } else {
              return `${cronMinutes} ${cronHours} * */${monthInterval} ${cronDay}L`;
            }
          } else {
            const positionMapping: { [key: string]: string } = {
              first: '1',
              second: '2',
              third: '3',
              fourth: '4',
            };
            const occurrence = positionMapping[settings.weekPosition];
            if (monthInterval === 1) {
              return `${cronMinutes} ${cronHours} * * ${cronDay}#${occurrence}`;
            } else {
              return `${cronMinutes} ${cronHours} * */${monthInterval} ${cronDay}#${occurrence}`;
            }
          }
        } else {
          const dayOfMonth = settings.dayOfMonth || 1;
          const monthInterval = settings.monthsIntervalDayOfMonth || 1;
          if (monthInterval === 1) {
            return `${cronMinutes} ${cronHours} ${dayOfMonth} * *`;
          } else {
            return `${cronMinutes} ${cronHours} ${dayOfMonth} */${monthInterval} *`;
          }
        }

      case 'years':
        const monthMapping: { [key: string]: string } = {
          January: '1',
          February: '2',
          March: '3',
          April: '4',
          May: '5',
          June: '6',
          July: '7',
          August: '8',
          September: '9',
          October: '10',
          November: '11',
          December: '12',
        };

        if (
          settings.yearlyType === 'weekPosition' &&
          settings.weekPosition &&
          settings.dayOfWeek
        ) {
          const dayMapping: { [key: string]: string } = {
            monday: '1',
            tuesday: '2',
            wednesday: '3',
            thursday: '4',
            friday: '5',
            saturday: '6',
            sunday: '0',
          };
          const cronDay = dayMapping[settings.dayOfWeek];
          const cronMonth = monthMapping[settings.monthOfYear || 'March'];

          if (settings.weekPosition === 'last') {
            return `${cronMinutes} ${cronHours} * ${cronMonth} ${cronDay}L`;
          } else {
            const positionMapping: { [key: string]: string } = {
              first: '1',
              second: '2',
              third: '3',
              fourth: '4',
            };
            const occurrence = positionMapping[settings.weekPosition];
            return `${cronMinutes} ${cronHours} * ${cronMonth} ${cronDay}#${occurrence}`;
          }
        } else {
          const cronMonth = monthMapping[settings.monthOfYear || 'December'];
          const dayOfMonth = settings.dayOfMonth || 1;
          return `${cronMinutes} ${cronHours} ${dayOfMonth} ${cronMonth} *`;
        }

      default:
        return `${cronMinutes} ${cronHours} * * *`;
    }
  };

  const handleSave = () => {
    if (!sourceId) return;

    const cronExpression = generateCronExpression();

    updateMutation.mutate({
      baseId: sourceId,
      cronSchedule: cronExpression,
      updateAutomatically: settings.updateAutomatically,
    });
  };

  const handleCancel = () => {
    navigate(-1);
  };

  const handleUpdateManually = async () => {
    if (!sourceId) return;

    try {
      await apiDev.post('/source/refresh', { baseId: sourceId });
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.manualUpdateStarted'),
      });
    } catch (error) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.manualUpdateError'),
      });
    }
  };

  const repeatOptions = [
    { label: t('knowledgeBase.daily'), value: 'days' },
    { label: t('knowledgeBase.weekly'), value: 'weeks' },
    { label: t('knowledgeBase.monthly'), value: 'months' },
    { label: t('knowledgeBase.yearly'), value: 'years' },
  ];

  const daysOfWeekOptions = [
    { label: t('settings.weekdays.monday'), value: 'Mon' },
    { label: t('settings.weekdays.tuesday'), value: 'Tue' },
    { label: t('settings.weekdays.wednesday'), value: 'Wed' },
    { label: t('settings.weekdays.thursday'), value: 'Thu' },
    { label: t('settings.weekdays.friday'), value: 'Fri' },
    { label: t('settings.weekdays.saturday'), value: 'Sat' },
    { label: t('settings.weekdays.sunday'), value: 'Sun' },
  ];

  const monthOptions = [
    { label: 'January', value: 'January' },
    { label: 'February', value: 'February' },
    { label: 'March', value: 'March' },
    { label: 'April', value: 'April' },
    { label: 'May', value: 'May' },
    { label: 'June', value: 'June' },
    { label: 'July', value: 'July' },
    { label: 'August', value: 'August' },
    { label: 'September', value: 'September' },
    { label: 'October', value: 'October' },
    { label: 'November', value: 'November' },
    { label: 'December', value: 'December' },
  ];

  const weekPositionOptions = [
    { label: t('knowledgeBase.first'), value: 'first' },
    { label: t('knowledgeBase.second'), value: 'second' },
    { label: t('knowledgeBase.third'), value: 'third' },
    { label: t('knowledgeBase.fourth'), value: 'fourth' },
    { label: t('knowledgeBase.last'), value: 'last' },
  ];

  const dayOfWeekOptions = [
    { label: t('settings.weekdays.monday'), value: 'monday' },
    { label: t('settings.weekdays.tuesday'), value: 'tuesday' },
    { label: t('settings.weekdays.wednesday'), value: 'wednesday' },
    { label: t('settings.weekdays.thursday'), value: 'thursday' },
    { label: t('settings.weekdays.friday'), value: 'friday' },
    { label: t('settings.weekdays.saturday'), value: 'saturday' },
    { label: t('settings.weekdays.sunday'), value: 'sunday' },
  ];

  const numberOptions = [
    { label: '1', value: '1' },
    { label: '2', value: '2' },
    { label: '3', value: '3' },
    { label: '4', value: '4' },
    { label: '5', value: '5' },
    { label: '6', value: '6' },
  ];

  const renderScheduleFields = () => {
    switch (settings.repeatUnit) {
      case 'days':
        return null;

      case 'weeks':
        return (
          <div className="knowledge-base-settings__section">
            <label className="knowledge-base-settings__label">
              {t('knowledgeBase.dayOfUpdate')}
            </label>
            <div className="knowledge-base-settings__days-grid">
              {daysOfWeekOptions.map((day) => (
                <Button
                  style={{
                    borderRadius: '50%',
                    minHeight: 40,
                    maxHeight: 40,
                    maxWidth: 40,
                    minWidth: 40,
                    padding: 0,
                    fontSize: 14,
                    justifyContent: 'center',
                  }}
                  key={day.value}
                  appearance={
                    settings.daysOfWeek?.includes(day.value)
                      ? 'primary'
                      : 'secondary'
                  }
                  size="s"
                  onClick={() => {
                    const isCurrentlySelected = settings.daysOfWeek?.includes(
                      day.value
                    );
                    if (isCurrentlySelected) {
                      setSettings((prev) => ({ ...prev, daysOfWeek: [] }));
                    } else {
                      setSettings((prev) => ({
                        ...prev,
                        daysOfWeek: [day.value],
                      }));
                    }
                  }}
                >
                  {day.value}
                </Button>
              ))}
            </div>
          </div>
        );

      case 'months':
        return (
          <div
            className="knowledge-base-settings__section"
            style={{
              flexDirection: 'column',
              marginLeft: 318,
              gap: 24,
              alignItems: 'flex-start',
            }}
          >
            <Track gap={8} align="center" style={{ width: 'fit-content' }}>
              <input
                style={{ minWidth: 20 }}
                type="radio"
                id="day-of-month"
                name="month-schedule"
                checked={settings.monthlyType === 'dayOfMonth'}
                onChange={() =>
                  setSettings((prev) => ({
                    ...prev,
                    monthlyType: 'dayOfMonth',
                    weekPosition: undefined,
                  }))
                }
              />
              <span>{t('knowledgeBase.day')}</span>
              <FormInput
                label=""
                name="dayOfMonth"
                type="number"
                min="1"
                max="31"
                value={settings.dayOfMonth?.toString() || '2'}
                hideLabel
                style={{ width: 56 }}
                onChange={(e) =>
                  setSettings((prev) => ({
                    ...prev,
                    dayOfMonth: parseInt(e.target.value),
                  }))
                }
              />
              <span style={{ width: 59, minWidth: 59 }}>
                {t('knowledgeBase.ofevery')}
              </span>
              <FormSelect
                label=""
                name="monthsIntervalDayOfMonth"
                hideLabel
                options={numberOptions}
                style={{ minWidth: 56, width: 56 }}
                defaultValue={
                  settings.monthsIntervalDayOfMonth?.toString() || '2'
                }
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    monthsIntervalDayOfMonth: parseInt(option?.value || '2'),
                  }))
                }
              />
              <span> {t('knowledgeBase.month')}</span>
            </Track>

            <Track gap={8} align="center">
              <input
                type="radio"
                id="week-position"
                name="month-schedule"
                style={{ minWidth: 20 }}
                checked={settings.monthlyType === 'weekPosition'}
                onChange={() =>
                  setSettings((prev) => ({
                    ...prev,
                    monthlyType: 'weekPosition',
                    weekPosition: 'last',
                  }))
                }
              />
              <FormSelect
                label=""
                name="weekPosition"
                hideLabel
                options={weekPositionOptions}
                style={{ width: 90 }}
                defaultValue={settings.weekPosition || 'last'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    weekPosition: option?.value as typeof settings.weekPosition,
                  }))
                }
              />
              <FormSelect
                label=""
                name="dayOfWeek"
                hideLabel
                style={{ width: 160 }}
                options={dayOfWeekOptions}
                defaultValue={settings.dayOfWeek || 'wednesday'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({ ...prev, dayOfWeek: option?.value }))
                }
              />
              <span style={{ width: 59, minWidth: 59 }}>
                {t('knowledgeBase.ofevery')}
              </span>
              <FormSelect
                label=""
                name="monthsIntervalWeekPosition"
                hideLabel
                options={numberOptions}
                style={{ width: 56 }}
                defaultValue={
                  settings.monthsIntervalWeekPosition?.toString() || '2'
                }
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    monthsIntervalWeekPosition: parseInt(option?.value || '2'),
                  }))
                }
              />
              <span> {t('knowledgeBase.month')}</span>
            </Track>
          </div>
        );

      case 'years':
        return (
          <div
            className="knowledge-base-settings__section"
            style={{
              flexDirection: 'column',
              marginLeft: 318,
              gap: 24,
              alignItems: 'flex-start',
            }}
          >
            <Track gap={8} align="center">
              <input
                type="radio"
                style={{ minWidth: 20 }}
                id="month-day"
                name="year-schedule"
                checked={settings.yearlyType === 'dayOfMonth'}
                onChange={() =>
                  setSettings((prev) => ({
                    ...prev,
                    yearlyType: 'dayOfMonth',
                    weekPosition: undefined,
                  }))
                }
              />
              <FormSelect
                label=""
                name="monthOfYear"
                hideLabel
                options={monthOptions}
                style={{ minWidth: 260 }}
                defaultValue={settings.monthOfYear || 'December'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    monthOfYear: option?.value,
                  }))
                }
              />
              <FormInput
                label=""
                name="dayOfMonth"
                type="number"
                min="1"
                max="31"
                value={settings.dayOfMonth?.toString() || '2'}
                hideLabel
                style={{ width: 56 }}
                onChange={(e) =>
                  setSettings((prev) => ({
                    ...prev,
                    dayOfMonth: parseInt(e.target.value),
                  }))
                }
              />
            </Track>

            <Track gap={8} align="center">
              <input
                type="radio"
                id="week-position-year"
                name="year-schedule"
                style={{ minWidth: 20 }}
                checked={settings.yearlyType === 'weekPosition'}
                onChange={() =>
                  setSettings((prev) => ({
                    ...prev,
                    yearlyType: 'weekPosition',
                    weekPosition: 'last',
                  }))
                }
              />
              <FormSelect
                label=""
                name="weekPosition"
                hideLabel
                options={weekPositionOptions}
                style={{ minWidth: 260 }}
                defaultValue={settings.weekPosition || 'last'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    weekPosition: option?.value as typeof settings.weekPosition,
                  }))
                }
              />
              <FormSelect
                label=""
                name="dayOfWeek"
                hideLabel
                style={{ minWidth: 260 }}
                options={dayOfWeekOptions}
                defaultValue={settings.dayOfWeek || 'wednesday'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({ ...prev, dayOfWeek: option?.value }))
                }
              />
              <FormSelect
                label=""
                name="monthOfYear"
                hideLabel
                options={monthOptions}
                style={{ minWidth: 260 }}
                defaultValue={settings.monthOfYear || 'March'}
                onSelectionChange={(option) =>
                  setSettings((prev) => ({
                    ...prev,
                    monthOfYear: option?.value,
                  }))
                }
              />
            </Track>
          </div>
        );

      default:
        return null;
    }
  };

  if (isLoading) {
    return <div>Loading...</div>;
  }
  return (
    <div className="knowledge-base-settings">
      <h1 style={{ marginBottom: 16 }} className="h1">
        {t('knowledgeBase.scrapeSettings')}
      </h1>
      <Card
        footer={
          <div>
            <Track gap={16} justify="between">
              <Button
                appearance="secondary"
                style={{
                  color: '#005AA3',
                  borderColor: '#005AA3 !important',
                  boxShadow: 'inset 0 0 0 2px #005AA3',
                }}
                onClick={handleCancel}
                disabled={updateMutation.isLoading}
              >
                {t('global.cancel')}
              </Button>
              <Button
                appearance="primary"
                onClick={handleSave}
                disabled={updateMutation.isLoading}
              >
                {updateMutation.isLoading
                  ? t('global.saving')
                  : t('global.save')}
              </Button>
            </Track>
          </div>
        }
      >
        <div className="knowledge-base-settings__content">
          <div className="knowledge-base-settings__section">
            <label className="knowledge-base-settings__label">
              {t('knowledgeBase.url')}
            </label>
            <div
              className="knowledge-base-settings__url"
              style={{ fontWeight: 'bold' }}
            >
              {settings.url}
            </div>
          </div>

          <div className="knowledge-base-settings__section">
            <label className="knowledge-base-settings__label">
              {t('knowledgeBase.updateAutomatically')}
            </label>
            <SwitchBox
              label=""
              checked={settings.updateAutomatically}
              onCheckedChange={(checked) => {
                setSettings((prev) => ({
                  ...prev,
                  updateAutomatically: checked,
                }));
              }}
            />
          </div>

          {settings.updateAutomatically && (
            <>
              <div className="knowledge-base-settings__section">
                <div className="knowledge-base-settings__repeat-group">
                  <label className="knowledge-base-settings__label">
                    {t('knowledgeBase.repeat')}
                  </label>
                  <Track gap={16} align="center">
                    <FormSelect
                      label=""
                      name="repeatUnit"
                      hideLabel
                      options={repeatOptions}
                      style={{ minWidth: 260 }}
                      defaultValue={settings.repeatUnit}
                      onSelectionChange={(option) =>
                        setSettings((prev) => ({
                          ...prev,
                          repeatUnit:
                            option?.value as typeof settings.repeatUnit,
                        }))
                      }
                    />
                  </Track>
                </div>
              </div>

              {renderScheduleFields()}

              <div className="knowledge-base-settings__section">
                <label className="knowledge-base-settings__label">
                  {t('knowledgeBase.timeOfUpdate')}
                </label>
                <FormInput
                  label={''}
                  name="timeOfUpdate"
                  type="text"
                  value={settings.timeOfUpdate}
                  placeholder="hh:mm"
                  style={{ width: 120 }}
                  onChange={(e) => {
                    let value = e.target.value;
                    let digitsOnly = value.replace(/[^\d]/g, '');

                    if (digitsOnly.length === 0) {
                      value = '';
                    } else if (digitsOnly.length === 1) {
                      let firstDigit = parseInt(digitsOnly);
                      if (firstDigit > 2) {
                        value = '0' + firstDigit;
                      } else {
                        value = digitsOnly;
                      }
                    } else if (digitsOnly.length === 2) {
                      let hours = parseInt(digitsOnly);
                      if (hours > 23) {
                        value =
                          '0' +
                          digitsOnly.charAt(0) +
                          ':' +
                          digitsOnly.charAt(1);
                      } else {
                        value = digitsOnly;
                      }
                    } else if (digitsOnly.length === 3) {
                      let hours = digitsOnly.substring(0, 2);
                      let minute = digitsOnly.charAt(2);

                      if (parseInt(hours) > 23) {
                        hours = '0' + digitsOnly.charAt(0);
                        minute = digitsOnly.charAt(1);
                      }

                      if (parseInt(minute) > 5) {
                        minute = '0' + minute;
                      }

                      value = hours + ':' + minute;
                    } else if (digitsOnly.length >= 4) {
                      let hours = digitsOnly.substring(0, 2);
                      let minutes = digitsOnly.substring(2, 4);

                      if (parseInt(hours) > 23) {
                        hours = '0' + digitsOnly.charAt(0);
                        minutes = digitsOnly.charAt(1) + digitsOnly.charAt(2);
                      }

                      if (parseInt(minutes) > 59) {
                        minutes = '0' + minutes.charAt(0);
                      }

                      value = hours + ':' + minutes;
                    }

                    setSettings((prev) => ({
                      ...prev,
                      timeOfUpdate: value,
                    }));
                  }}
                >
                  <Icon icon={<MdOutlineSchedule />} size="medium" />
                </FormInput>
              </div>
            </>
          )}

          <div className="knowledge-base-settings__section">
            <label className="knowledge-base-settings__label">
              {t('knowledgeBase.updateManually')}
            </label>
            <Button appearance="primary" onClick={handleUpdateManually}>
              {t('knowledgeBase.updateData')}
            </Button>
          </div>
        </div>
      </Card>
    </div>
  );
};

export default SourceSettings;
