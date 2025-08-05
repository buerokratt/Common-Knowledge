import { FC, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { MdOutlineDeleteOutline } from 'react-icons/md';
import { Button, Card, DataTable, Dialog, Icon, Track } from 'components';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  ColumnFiltersState,
} from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import { Link } from 'react-router-dom';
import {
  getAgencies,
  deleteAgency,
  Agency,
  AgencyListParams,
} from 'services/agencies';
import './AgencyList.scss';

const AgencyComponent: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();

  // Keep all your existing modal states
  const [uploadModal, setUploadModal] = useState(false);
  const [addApiModal, setAddApiModal] = useState(false);
  const [addUrlModal, setAddUrlModal] = useState(false);
  const [deleteModal, setDeleteModal] = useState<Agency | null>(null);

  // Add table state for server-side pagination and sorting
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  // Convert sorting state to API format
  const getSortingParam = (sorting: SortingState): string => {
    if (sorting.length === 0) return 'updatedAt desc';

    const sort = sorting[0];
    let field = sort.id;

    // Map column IDs to API field names
    const fieldMap: Record<string, string> = {
      agency: 'name',
      domain: 'sector',
      lastUpdate: 'updatedAt',
    };

    field = fieldMap[field] || field;
    return `${field} ${sort.desc ? 'desc' : 'asc'}`;
  };

  // API query parameters
  const queryParams: AgencyListParams = useMemo(
    () => ({
      page: pagination.pageIndex + 1,
      pageSize: pagination.pageSize,
      sorting: getSortingParam(sorting),
    }),
    [pagination.pageIndex, pagination.pageSize, sorting]
  );

  // Fetch agencies data
  const {
    data: agencyApiData,
    isLoading,
    error,
    refetch,
  } = useQuery({
    queryKey: ['agencies', queryParams],
    queryFn: () => getAgencies(queryParams),
    keepPreviousData: true,
  });

  // Delete agency mutation
  const deleteAgencyMutation = useMutation({
    mutationFn: deleteAgency,
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.deleteSuccess'),
      });
      setDeleteModal(null);
      queryClient.invalidateQueries(['agencies']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.deleteError'),
      });
    },
  });

  // Transform API data to match your existing KnowledgeBaseItem interface
  const knowledgeBaseData = useMemo(() => {
    if (!agencyApiData) return { data: [], total: 0 };

    return {
      data: agencyApiData.data,
      total: agencyApiData.total,
    };
  }, [agencyApiData]);

  // Handle pagination change
  const handlePaginationChange = (newPagination: PaginationState) => {
    setPagination(newPagination);
  };

  // Handle sorting change
  const handleSortingChange = (newSorting: SortingState) => {
    setSorting(newSorting);
  };

  // Handle delete confirmation
  const handleDeleteConfirm = () => {
    if (deleteModal) {
      deleteAgencyMutation.mutate(deleteModal.baseId);
    }
  };

  // Keep your existing columns definition with updated delete handler
  const columns: ColumnDef<Agency>[] = [
    {
      accessorKey: 'name',
      header: t('knowledgeBase.agency'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <Link
          to={`/agency/${row.original.baseId}`}
          style={{ textDecoration: 'underline', color: '#005AA3' }}
        >
          <div className="agencies__agency-cell">{row.original.name}</div>
        </Link>
      ),
    },
    {
      accessorKey: 'sector',
      header: t('knowledgeBase.sector'),
      enableColumnFilter: false,
    },
    {
      accessorKey: 'updatedAt',
      header: t('knowledgeBase.lastUpdate'),
      enableColumnFilter: false,
      cell: ({ row }) =>
        new Date(row.original.updatedAt).toLocaleDateString('et-EE', {
          day: '2-digit',
          month: '2-digit',
          year: 'numeric',
        }),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track gap={32} justify="end">
          <Button
            appearance="text"
            onClick={() => setDeleteModal(row.original)}
            className="agencies__action-btn"
            disabled={deleteAgencyMutation.isLoading}
          >
            <Icon
              icon={<MdOutlineDeleteOutline fontSize={20} />}
              size="medium"
            />
            {t('global.delete')}
          </Button>
        </Track>
      ),
    },
  ];

  const agencyOptions = [
    { label: 'Abc', value: 'abc' },
    { label: 'Pvc', value: 'pvc' },
    { label: 'Xyz', value: 'xyz' },
  ];

  const domainOptions = [
    { label: 'Domain 1', value: 'domain1' },
    { label: 'Domain 2', value: 'domain2' },
    { label: 'Domain 3', value: 'domain3' },
  ];

  // Show loading state
  if (isLoading) {
    return <div>Loading...</div>;
  }

  // Show error state
  if (error) {
    return <div>Error loading agencies: {error.message}</div>;
  }

  return (
    <div className="agencies">
      <Track
        style={{ marginBottom: 16, minWidth: 800 }}
        justify="between"
        align="center"
      >
        <h1 className="h1">{t('knowledgeBase.agencies')}</h1>
        <Track gap={12}>
          <Link to="/agency/add">
            <Button appearance="primary">{t('knowledgeBase.addAgency')}</Button>
          </Link>
        </Track>
      </Track>

      <Card>
        <DataTable
          data={knowledgeBaseData?.data ?? []}
          columns={columns}
          pagination={pagination}
          setPagination={handlePaginationChange}
          sorting={sorting}
          setSorting={handleSortingChange}
          columnFilters={columnFilters}
          setFiltering={setColumnFilters}
          sortable
          filterable
          pagesCount={agencyApiData?.totalPages ?? 0}
          isClientSide={false}
        />

        <div className="agencies__footer">
          <span className="agencies__total">
            {knowledgeBaseData?.total ?? 0} {t('knowledgeBase.results')}
          </span>
        </div>
      </Card>

      {/* Delete Confirmation Modal */}
      {deleteModal && (
        <Dialog
          title={t('knowledgeBase.deleteAgencyTitle')}
          onClose={() => setDeleteModal(null)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setDeleteModal(null)}
                disabled={deleteAgencyMutation.isLoading}
              >
                {t('global.cancel')}
              </Button>
              <Button
                appearance="error"
                onClick={handleDeleteConfirm}
                disabled={deleteAgencyMutation.isLoading}
              >
                {deleteAgencyMutation.isLoading
                  ? t('global.deleting')
                  : t('global.delete')}
              </Button>
            </Track>
          }
        >
          <p>
            {t('knowledgeBase.deleteAgencyConfirmation', {
              agency: deleteModal.name,
              domain: deleteModal.sector,
            })}
          </p>
        </Dialog>
      )}
    </div>
  );
};

export default AgencyComponent;
