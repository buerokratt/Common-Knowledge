import { FC, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import {
  MdOutlineDeleteOutline,
  MdAccessTime,
  MdOutlineEdit,
  MdRefresh,
  MdOutlineStopCircle,
} from 'react-icons/md';
import {
  Button,
  Card,
  DataTable,
  Dialog,
  FormInput,
  Icon,
  Track,
  FileUploader,
  Tooltip,
} from 'components';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  ColumnFiltersState,
} from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import './Agency.scss';
import './AgencyList.scss';
import EditAgency from './SaveAgency';
import type {
  FileItem,
  UploadProgress,
} from 'components/FileUploader/FileUploader';
import { getAgency } from 'services/agencies';
import {
  getSources,
  createSourceFile,
  createSourceUrl,
  updateSourceSubsector,
  deleteSource,
  stopSourceScraping,
  refreshSource,
  Source,
  SourcesListParams,
  CreateSourceFileRequest,
} from 'services/sources';

interface KnowledgeBaseFormData {
  subsector: string;
  files: FileItem[];
  apiUrl?: string;
  websiteUrl?: string;
}

const Agency: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { id: agencyBaseId } = useParams<{ id: string }>();
  const [uploadProgress, setUploadProgress] = useState<UploadProgress>({
    isUploading: false,
    currentFile: 0,
    totalFiles: 0,
    currentFileName: '',
  });

  const [uploadModal, setUploadModal] = useState(false);
  const [addUrlModal, setAddUrlModal] = useState(false);
  const [editModal, setEditModal] = useState<Source | null>(null);
  const [deleteModal, setDeleteModal] = useState<Source | null>(null);

  // Add table state for server-side pagination and sorting
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

  const [formData, setFormData] = useState<KnowledgeBaseFormData>({
    subsector: '',
    files: [],
  });

  // Convert sorting state to API format
  const getSortingParam = (sorting: SortingState): string => {
    if (sorting.length === 0) return 'last_scraped_at desc';

    const sort = sorting[0];
    let field = sort.id;

    // Map column IDs to API field names
    const fieldMap: Record<string, string> = {
      url: 'url',
      subsector: 'subsector',
      lastScraped: 'last_scraped_at',
      status: 'status',
    };

    field = fieldMap[field] || field;
    return `${field} ${sort.desc ? 'desc' : 'asc'}`;
  };

  // API query parameters
  const queryParams: SourcesListParams = useMemo(
    () => ({
      agencyBaseId: agencyBaseId!,
      page: pagination.pageIndex + 1,
      pageSize: pagination.pageSize,
      sorting: getSortingParam(sorting),
    }),
    [agencyBaseId, pagination.pageIndex, pagination.pageSize, sorting]
  );

  // Fetch agency data
  const { data: agencyData, isLoading: isLoadingAgency } = useQuery({
    queryKey: ['agency', agencyBaseId],
    queryFn: () => getAgency(agencyBaseId!),
    enabled: !!agencyBaseId,
  });

  // Fetch sources data
  const {
    data: sourcesData,
    isLoading: isLoadingSources,
    refetch,
  } = useQuery({
    queryKey: ['sources', queryParams],
    queryFn: () => getSources(queryParams),
    enabled: !!agencyBaseId,
    keepPreviousData: true,
  });

  // File upload mutation
  const uploadMutation = useMutation({
    mutationFn: async (data: CreateSourceFileRequest) => {
      // Set initial upload state
      setUploadProgress({
        isUploading: true,
        currentFile: 0,
        totalFiles: data.files.length,
        currentFileName: '',
      });

      return createSourceFile(
        data,
        (
          fileIndex: number,
          fileName: string,
          status: 'uploading' | 'success'
        ) => {
          // Update overall progress - show which file is currently being uploaded
          setUploadProgress((prev) => ({
            ...prev,
            currentFile:
              status === 'uploading' ? fileIndex + 1 : prev.currentFile,
            currentFileName:
              status === 'uploading' ? fileName : prev.currentFileName,
          }));

          // Update individual file status in real-time
          setFormData((prev) => ({
            ...prev,
            files: prev.files.map((file, index) => {
              if (index === fileIndex) {
                return {
                  ...file,
                  status: status as any,
                };
              }
              return file;
            }),
          }));
        }
      );
    },
    onMutate: () => {
      // Set all valid files to uploading status
      setFormData((prev) => ({
        ...prev,
        files: prev.files.map((file) =>
          file.status !== 'error'
            ? { ...file, status: 'uploading' as const }
            : file
        ),
      }));
    },
    onSuccess: () => {
      // Reset upload progress
      setUploadProgress({
        isUploading: false,
        currentFile: 0,
        totalFiles: 0,
        currentFileName: '',
      });

      // All files should already be marked as success from the progress callback
      // But ensure any remaining files are marked as success
      setFormData((prev) => ({
        ...prev,
        files: prev.files.map((file) => ({
          ...file,
          status:
            file.status === 'uploading' ? ('success' as const) : file.status,
        })),
      }));

      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.uploadSuccess'),
      });

      // Close modal after a short delay to show success state
      setTimeout(() => {
        setUploadModal(false);
        setFormData({ subsector: '', files: [] });
      }, 1000);

      queryClient.invalidateQueries(['sources']);
    },
    onError: (error: any) => {
      // Reset upload progress
      setUploadProgress({
        isUploading: false,
        currentFile: 0,
        totalFiles: 0,
        currentFileName: '',
      });

      // Set failed files to error status
      setFormData((prev) => ({
        ...prev,
        files: prev.files.map((file) => ({
          ...file,
          status:
            file.status === 'uploading' ? ('error' as const) : file.status,
          message: file.status === 'uploading' ? error.message : file.message,
        })),
      }));

      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.uploadError'),
      });
    },
  });

  // URL addition mutation
  const addUrlMutation = useMutation({
    mutationFn: createSourceUrl,
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlSuccess'),
      });
      setAddUrlModal(false);
      setFormData({ subsector: '', files: [] });
      queryClient.invalidateQueries(['sources']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.urlError'),
      });
    },
  });

  // Update source mutation
  const updateMutation = useMutation({
    mutationFn: ({ id, data }: { id: string; data: any }) =>
      updateSourceSubsector(id, data),
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.updateSuccess'),
      });
      setEditModal(null);
      setFormData({ subsector: '', files: [] });
      queryClient.invalidateQueries(['sources']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.updateError'),
      });
    },
  });

  // Delete source mutation
  const deleteMutation = useMutation({
    mutationFn: deleteSource,
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.deleteSuccess'),
      });
      setDeleteModal(null);
      queryClient.invalidateQueries(['sources']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.deleteError'),
      });
    },
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
      queryClient.invalidateQueries(['sources']);
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
      queryClient.invalidateQueries(['sources']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.refreshError'),
      });
    },
  });

  const handleUpload = () => {
    if (!agencyBaseId || formData.files.length === 0 || !formData.subsector) {
      return;
    }

    // Extract actual File objects from FileItem[]
    const files = formData.files
      .filter((fileItem) => fileItem.status !== 'error')
      .map((fileItem) => fileItem.file);

    uploadMutation.mutate({
      agencyBaseId,
      subsector: formData.subsector,
      type: 'file',
      files,
    });
  };

  const handleAddUrl = () => {
    if (!agencyBaseId || !formData.websiteUrl || !formData.subsector) {
      return;
    }

    addUrlMutation.mutate({
      agencyBaseId,
      url: formData.websiteUrl,
      subsector: formData.subsector,
      type: 'url',
    });
  };

  const handleFilesChange = (files: FileItem[]) => {
    setFormData((prev) => ({
      ...prev,
      files,
    }));
  };

  const handleFileDelete = (fileId: string) => {
    setFormData((prev) => ({
      ...prev,
      files: prev.files.filter((file) => file.id !== fileId),
    }));
  };

  const handleEdit = (item: Source) => {
    setEditModal(item);
    setFormData({
      subsector: item.subsector,
      files: [],
    });
  };

  const handleUpdateItem = () => {
    if (!editModal) return;

    updateMutation.mutate({
      id: editModal.baseId,
      data: {
        subsector: formData.subsector,
      },
    });
  };

  const handleDelete = () => {
    if (!deleteModal) return;
    deleteMutation.mutate(deleteModal.baseId);
  };

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

  const columns: ColumnDef<Source>[] = [
    {
      accessorKey: 'url',
      header: t('knowledgeBase.url'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <Link
          to={`/source/${row.original.baseId}/files`}
          style={{ textDecoration: 'underline', color: '#005AA3' }}
        >
          <Tooltip content={row.original.url}>
            <div
              className="agencies__agency-cell"
              style={{
                maxWidth: 250,
                textOverflow: 'ellipsis',
                overflow: 'hidden',
              }}
            >
              {row.original.url}
            </div>
          </Tooltip>
        </Link>
      ),
    },
    {
      accessorKey: 'subsector',
      header: t('knowledgeBase.subsector'),
      enableColumnFilter: false,
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
            color: row.original.status === 'running' ? '#005AA3' : '#266B42',
            borderColor:
              row.original.status === 'running' ? '#005AA3' : '#266B42',
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
              disabled={
                refreshMutation.isLoading || row.original.type === 'file'
              }
            >
              <Icon icon={<MdRefresh fontSize={20} />} size="medium" />
              {t('knowledgeBase.refresh')}
            </Button>
          )}

          <Link
            style={{ display: 'flex', textDecoration: 'none' }}
            to={`/source/${row.original.baseId}/schedule`}
          >
            <Button
              disabled={
                row.original.status === 'running' ||
                row.original.type === 'file'
              }
              appearance="text"
              className="agencies__action-btn"
            >
              <Icon icon={<MdAccessTime fontSize={20} />} size="medium" />
              {t('knowledgeBase.scrapeInterval')}
            </Button>
          </Link>
          <Button
            disabled={row.original.status === 'running'}
            appearance="text"
            className="agencies__action-btn"
            onClick={() => handleEdit(row.original)}
          >
            <Icon icon={<MdOutlineEdit fontSize={20} />} size="medium" />
            {t('global.edit')}
          </Button>
          <Button
            disabled={row.original.status === 'running'}
            appearance="text"
            className="agencies__action-btn"
            onClick={() => setDeleteModal(row.original)}
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

  // Show loading state
  if (isLoadingAgency || isLoadingSources) {
    return <div>Loading...</div>;
  }

  return (
    <div className="agency-container">
      <EditAgency />
      <Card
        header={
          <Track justify="between" align="center">
            <span className="knowledge-base-detail__agency">
              {t('knowledgeBase.sources')}
            </span>
            <Track gap={16}>
              <Button
                appearance="secondary"
                style={{
                  color: '#005AA3',
                  borderColor: '#005AA3 !important',
                  boxShadow: 'inset 0 0 0 2px #005AA3',
                }}
                onClick={() => setUploadModal(true)}
              >
                {t('knowledgeBase.uploadFiles')}
              </Button>
              <Button appearance="primary" onClick={() => setAddUrlModal(true)}>
                {t('knowledgeBase.addUrl')}
              </Button>
            </Track>
          </Track>
        }
      >
        <DataTable
          data={sourcesData?.data ?? []}
          columns={columns}
          pagination={pagination}
          setPagination={handlePaginationChange}
          sorting={sorting}
          setSorting={handleSortingChange}
          columnFilters={columnFilters}
          setFiltering={setColumnFilters}
          sortable
          filterable
          pagesCount={sourcesData?.totalPages ?? 0}
          isClientSide={false}
        />

        <div className="agencies__footer">
          <span className="agencies__total">
            {sourcesData?.total ?? 0} {t('knowledgeBase.results')}
          </span>
        </div>
      </Card>

      {/* Upload Modal */}
      {uploadModal && (
        <Dialog
          title={t('knowledgeBase.uploadFiles')}
          onClose={() => !uploadProgress.isUploading && setUploadModal(false)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setUploadModal(false)}
                disabled={uploadProgress.isUploading}
              >
                {t('global.cancel')}
              </Button>
              <Button
                appearance="primary"
                onClick={handleUpload}
                disabled={
                  uploadProgress.isUploading ||
                  !formData.subsector ||
                  formData.files.length === 0 ||
                  formData.files.every((file) => file.status === 'error')
                }
              >
                {uploadProgress.isUploading
                  ? t('fileUpload.uploading')
                  : t('knowledgeBase.upload')}
              </Button>
            </Track>
          }
        >
          <Track direction="vertical" gap={16}>
            <FormInput
              className="url-input"
              label={t('knowledgeBase.subsector')}
              name="subsector"
              value={formData.subsector}
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, subsector: e.target.value }))
              }
              required
              disabled={uploadProgress.isUploading}
            />
            <FileUploader
              files={formData.files}
              onFilesChange={handleFilesChange}
              onFileDelete={handleFileDelete}
              maxFileSize={30 * 1024 * 1024} // 30MB
              acceptedTypes=".pdf,.doc,.docx,.html,.htm"
              multiple={true}
              uploadProgress={uploadProgress} // Pass upload progress to FileUploader
            />
          </Track>
        </Dialog>
      )}

      {/* Add URL Modal */}
      {addUrlModal && (
        <Dialog
          title={t('knowledgeBase.addUrl')}
          onClose={() => setAddUrlModal(false)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setAddUrlModal(false)}
              >
                {t('global.cancel')}
              </Button>
              <Button
                appearance="primary"
                onClick={handleAddUrl}
                disabled={
                  addUrlMutation.isLoading ||
                  !formData.subsector ||
                  !formData.websiteUrl
                }
              >
                {addUrlMutation.isLoading
                  ? t('global.adding')
                  : t('global.add')}
              </Button>
            </Track>
          }
        >
          <Track direction="vertical" gap={16}>
            <FormInput
              className="url-input"
              label={t('knowledgeBase.subsector')}
              name="subsector"
              value={formData.subsector}
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, subsector: e.target.value }))
              }
              required
            />
            <FormInput
              className="url-input"
              label={t('knowledgeBase.url')}
              name="websiteUrl"
              value={formData.websiteUrl || ''}
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, websiteUrl: e.target.value }))
              }
              required
            />
          </Track>
        </Dialog>
      )}

      {/* Edit Modal */}
      {editModal && (
        <Dialog
          title={t('knowledgeBase.editSource')}
          onClose={() => setEditModal(null)}
          footer={
            <Track gap={16} justify="end">
              <Button appearance="secondary" onClick={() => setEditModal(null)}>
                {t('global.cancel')}
              </Button>
              <Button
                appearance="primary"
                onClick={handleUpdateItem}
                disabled={updateMutation.isLoading || !formData.subsector}
              >
                {updateMutation.isLoading
                  ? t('global.saving')
                  : t('global.save')}
              </Button>
            </Track>
          }
        >
          <Track direction="vertical" gap={16}>
            <FormInput
              className="url-input"
              label={t('knowledgeBase.subsector')}
              name="subsector"
              value={formData.subsector}
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, subsector: e.target.value }))
              }
              required
            />
          </Track>
        </Dialog>
      )}

      {/* Delete Confirmation Modal */}
      {deleteModal && (
        <Dialog
          title={t('knowledgeBase.deleteSource')}
          onClose={() => setDeleteModal(null)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setDeleteModal(null)}
                disabled={deleteMutation.isLoading}
              >
                {t('global.cancel')}
              </Button>
              <Button
                appearance="error"
                onClick={handleDelete}
                disabled={deleteMutation.isLoading}
              >
                {deleteMutation.isLoading
                  ? t('global.deleting')
                  : t('global.delete')}
              </Button>
            </Track>
          }
        >
          {t('knowledgeBase.deleteSourceConfirmation')}
        </Dialog>
      )}
    </div>
  );
};

export default Agency;
