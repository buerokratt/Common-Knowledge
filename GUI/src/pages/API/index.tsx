import { FC, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { MdOutlineEdit, MdRefresh, MdOutlineStopCircle } from 'react-icons/md';
import { Button, Card, DataTable, Icon, Track } from 'components';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  ColumnFiltersState,
} from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { Link } from 'react-router-dom';
import {
  getApiIntegrations,
  stopSourceScraping,
  refreshSource,
  ApiIntegration,
  ApiIntegrationsListParams,
} from 'services/sources';
import 'pages/Agency/AgencyList.scss';

const ApiList: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();

  // Add table state for server-side pagination and sorting
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  // Convert sorting state to API format
  const getSortingParam = (sorting: SortingState): string => {
    if (sorting.length === 0) return 'last_scraped_at desc';

    const sort = sorting[0];
    let field = sort.id;

    // Map column IDs to API field names
    const fieldMap: Record<string, string> = {
      name: 'name',
      url: 'url',
      lastScraped: 'last_scraped_at',
      status: 'status',
    };

    field = fieldMap[field] || field;
    return `${field} ${sort.desc ? 'desc' : 'asc'}`;
  };

  // API query parameters
  const queryParams: ApiIntegrationsListParams = useMemo(
    () => ({
      page: pagination.pageIndex + 1,
      pageSize: pagination.pageSize,
      sorting: getSortingParam(sorting),
    }),
    [pagination.pageIndex, pagination.pageSize, sorting]
  );

  // Fetch API integrations data
  const {
    data: apiData,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['apiIntegrations', queryParams],
    queryFn: () => getApiIntegrations(queryParams),
    keepPreviousData: true,
  });

  // Stop scraping mutation
  const stopScrapingMutation = useMutation({
    mutationFn: stopSourceScraping,
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.stopSuccess'),
      });
      queryClient.invalidateQueries(['apiIntegrations']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.stopError'),
      });
    },
  });

  // Refresh source mutation
  const refreshMutation = useMutation({
    mutationFn: refreshSource,
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.refreshSuccess'),
      });
      queryClient.invalidateQueries(['apiIntegrations']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.refreshError'),
      });
    },
  });

  const handleStopScraping = (sourceId: string) => {
    stopScrapingMutation.mutate(sourceId);
  };

  const handleRefreshSource = (sourceId: string) => {
    refreshMutation.mutate(sourceId);
  };

  // Handle pagination change
  const handlePaginationChange = (newPagination: PaginationState) => {
    setPagination(newPagination);
  };

  // Handle sorting change
  const handleSortingChange = (newSorting: SortingState) => {
    setSorting(newSorting);
  };

  const columns: ColumnDef<ApiIntegration>[] = [
    {
      accessorKey: 'name',
      header: t('global.name'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <Link
          to={`/api/${row.original.baseId}`}
          style={{ textDecoration: 'underline', color: '#005AA3' }}
        >
          <div className="agencies__agency-cell">
            {row.original.name || row.original.url}
          </div>
        </Link>
      ),
    },
    {
      accessorKey: 'lastScrapedAt',
      header: t('knowledgeBase.lastScraped'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <span>
          {row.original.lastScrapedAt &&
            new Date(row.original.lastScrapedAt).toLocaleDateString('et-EE', {
              day: '2-digit',
              month: '2-digit',
              year: 'numeric',
            })}
        </span>
      ),
    },
    {
      accessorKey: 'status',
      header: t('global.status'),
      cell: ({ row }) => (
        <span
          className={`agencies__status-cell`}
          style={{
            color:
              row.original.status === 'running' || row.original.status === 'new'
                ? '#005AA3'
                : '#266B42',
            borderColor:
              row.original.status === 'running' || row.original.status === 'new'
                ? '#005AA3'
                : '#266B42',
          }}
        >
          {t(`knowledgeBase.${row.original.status}`)}
        </span>
      ),
      enableColumnFilter: false,
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track gap={32} justify="end">
          {row.original.status === 'running' ? (
            <Button
              className="agencies__action-btn"
              appearance="text"
              size="s"
              onClick={() => handleStopScraping(row.original.baseId)}
              disabled={stopScrapingMutation.isLoading}
            >
              <Icon
                icon={<MdOutlineStopCircle fontSize={20} />}
                size="medium"
              />
              {t('global.stop')}
            </Button>
          ) : (
            <Button
              className="agencies__action-btn"
              appearance="text"
              size="s"
              onClick={() => handleRefreshSource(row.original.baseId)}
              disabled={refreshMutation.isLoading}
            >
              <Icon icon={<MdRefresh fontSize={20} />} size="medium" />
              {t('knowledgeBase.refresh')}
            </Button>
          )}

          <Link
            style={{ display: 'flex', textDecoration: 'none' }}
            to={`/api/${row.original.baseId}/schedule`}
          >
            <Button
              disabled={row.original.status === 'running'}
              appearance="text"
              className="agencies__action-btn"
            >
              <Icon icon={<MdOutlineEdit fontSize={20} />} size="medium" />
              {t('knowledgeBase.scrapeInterval')}
            </Button>
          </Link>

          <Button
            disabled={row.original.status === 'running'}
            appearance="text"
            className="agencies__action-btn"
          >
            <Icon icon={<MdOutlineEdit fontSize={20} />} size="medium" />
            {t('global.edit')}
          </Button>
        </Track>
      ),
    },
  ];

  // Show loading state
  if (isLoading) {
    return <div>Loading...</div>;
  }

  // Show error state
  if (error) {
    return <div>Error loading API integrations: {error.message}</div>;
  }

  return (
    <div className="agencies">
      <Track
        style={{ marginBottom: 16, minWidth: 800 }}
        justify="between"
        align="center"
      >
        <h1 className="h1">{t('menu.apiIntegrations')}</h1>
      </Track>

      <Card>
        <DataTable
          data={apiData?.data ?? []}
          columns={columns}
          pagination={pagination}
          setPagination={handlePaginationChange}
          sorting={sorting}
          setSorting={handleSortingChange}
          columnFilters={columnFilters}
          setFiltering={setColumnFilters}
          sortable
          filterable
          pagesCount={apiData?.totalPages ?? 0}
          isClientSide={false}
        />

        <div className="agencies__footer">
          <span className="agencies__total">
            {apiData?.total ?? 0} {t('knowledgeBase.results')}
          </span>
        </div>
      </Card>
    </div>
  );
};

export default ApiList;
