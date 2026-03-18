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
  MdInfoOutline,
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
  createSourceWithUrlList,
  updateSourceSubsector,
  deleteSource,
  stopSourceScraping,
  refreshSource,
  Source,
  SourcesListParams,
  CreateSourceFileRequest,
  CreateSourceWithUrlListRequest,
} from 'services/sources';

interface KnowledgeBaseFormData {
  subsector: string;
  files: FileItem[];
  apiUrl?: string;
  websiteUrl?: string;
  qualityControlLevel: '' | 'basic' | 'comprehensive';
  csvFile?: File | null;
  urlList?: { url: string }[];
}

const getInitialFormData = (): KnowledgeBaseFormData => ({
  subsector: '',
  files: [],
  apiUrl: '',
  websiteUrl: '',
  qualityControlLevel: '',
  csvFile: null,
  urlList: [],
});

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
  const [addUrlListModal, setAddUrlListModal] = useState(false);
  const [editModal, setEditModal] = useState<Source | null>(null);
  const [deleteModal, setDeleteModal] = useState<Source | null>(null);

  // Add table state for server-side pagination and sorting
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });
  const [sorting, setSorting] = useState<SortingState>([]);
  const [columnFilters, setColumnFilters] = useState<ColumnFiltersState>([]);

   const [formData, setFormData] = useState<KnowledgeBaseFormData>(
    getInitialFormData()
  );

  // Convert sorting state to API format
  const getSortingParam = (sorting: SortingState): string => {
    if (sorting.length === 0) return '';

    const sort = sorting[0];
    let field = sort.id;

    // Map column IDs to API field names
    const fieldMap: Record<string, string> = {
      url: 'url',
      subsector: 'subsector',
      lastScrapedAt: 'last_scraped_at',
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

      setUploadModal(false);
      setFormData(getInitialFormData());

      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.uploadSuccess'),
      });

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
      setFormData(getInitialFormData);
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

  // URL list addition mutation
  const addUrlListMutation = useMutation({
    mutationFn: createSourceWithUrlList,
    onSuccess: (data: any) => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: `Successfully created source with ${data.urls_count || formData.urlList?.length || 0} URLs`,
      });
      setAddUrlListModal(false);
      setFormData(getInitialFormData);
      queryClient.invalidateQueries(['sources']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || 'Failed to create source with URL list',
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
      setFormData(getInitialFormData);
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
    onSuccess: async () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.deleteSuccess'),
      });
      setDeleteModal(null);

      // Refetch to get updated data
      await queryClient.invalidateQueries(['sources']);

      // Check if current page is now out of bounds
      const newTotal = (sourcesData?.total || 0) - 1;
      const maxPages = Math.ceil(newTotal / pagination.pageSize);

      // Reset to last valid page if current page is out of bounds
      if (pagination.pageIndex >= maxPages && maxPages > 0) {
        setPagination({
          ...pagination,
          pageIndex: maxPages - 1,
        });
      } else if (maxPages === 0) {
        // If no data left, reset to page 0
        setPagination({
          ...pagination,
          pageIndex: 0,
        });
      }
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
      qualityControlLevel: formData.qualityControlLevel,
    });
  };

  const handleCsvFileChange = async (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;

    try {
      const urls = await parseCSVFile(file);
      setFormData((prev) => ({
        ...prev,
        csvFile: file,
        urlList: urls,
      }));
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: `Parsed ${urls.length} URLs from CSV`,
      });
    } catch (error: any) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || 'Failed to parse CSV file',
      });
      event.target.value = '';
    }
  };

  const parseCSVFile = (file: File): Promise<{ url: string }[]> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      
      reader.onload = (e) => {
        try {
          const text = e.target?.result as string;
          const lines = text.split('\n').map(line => line.trim()).filter(line => line);
          
          if (lines.length === 0) {
            reject(new Error('CSV file is empty'));
            return;
          }
          
          // Check if first line is a header (contains 'url')
          const hasHeader = lines[0].toLowerCase().includes('url');
          const dataLines = hasHeader ? lines.slice(1) : lines;
          
          if (dataLines.length === 0) {
            reject(new Error('No URLs found in CSV'));
            return;
          }
          
          const urls = dataLines.map(line => {
            // Handle CSV with commas - take first column
            const url = line.split(',')[0].trim();
            return { url };
          }).filter(item => {
            // Validate URL format
            return item.url && (item.url.startsWith('http://') || item.url.startsWith('https://'));
          });
          
          if (urls.length === 0) {
            reject(new Error('No valid URLs found in CSV. URLs must start with http:// or https://'));
            return;
          }
          
          resolve(urls);
        } catch (error) {
          reject(new Error('Failed to parse CSV file'));
        }
      };
      
      reader.onerror = () => {
        reject(new Error('Failed to read CSV file'));
      };
      
      reader.readAsText(file);
    });
  };

  const handleAddUrlList = () => {
    if (!agencyBaseId || !formData.websiteUrl || !formData.subsector || !formData.urlList || formData.urlList.length === 0) {
      return;
    }

    addUrlListMutation.mutate({
      agencyBaseId,
      url: formData.websiteUrl,
      subsector: formData.subsector,
      type: 'specified',
      urls: formData.urlList,
      qualityControlLevel: formData.qualityControlLevel,
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
      ...getInitialFormData(),
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
            color: row.original.status === 'running'
              ? '#005AA3'
              : row.original.status === 'in_review'
              ? '#BA830D'
              : '#266B42',
            borderColor: row.original.status === 'running'
              ? '#005AA3'
              : row.original.status === 'in_review'
              ? '#BA830D'
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
              disabled={
                refreshMutation.isLoading || 
                row.original.type === 'file' ||
                (row.original.status === 'in_review' && !row.original.hasFinishedFiles)
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
              <Button 
                appearance="primary"
                style={{
                  backgroundColor: '#005AA3',
                }}
                onClick={() => setAddUrlListModal(true)}
              >
                {t('knowledgeBase.addUrlList')}
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
            <div className="quality-control-options">
              <span className="quality-control-options__title">
                {t('knowledgeBase.contentExtractionQualityControlOptions')}
              </span>
              <div className="quality-control-options__row">
                <label className="quality-control-options__item">
                  <input
                    type="radio"
                    name="qualityControlLevel"
                    checked={formData.qualityControlLevel === 'basic'}
                    onClick={() =>
                      setFormData((prev) => ({
                        ...prev,
                        qualityControlLevel:
                          prev.qualityControlLevel === 'basic' ? '' : 'basic',
                      }))
                    }
                    onChange={() => {}}
                  />
                  <span>{t('knowledgeBase.basicQualityControl')}</span>
                </label>
                <Tooltip content="Tooltip to be implemented">
                  <button
                    type="button"
                    className="quality-control-options__info-btn"
                    aria-label={t('knowledgeBase.basicQualityControlInfo') as string}
                  >
                    <Icon
                      className="quality-control-options__info"
                      icon={<MdInfoOutline fontSize={18} color="#005AA3" />}
                      size="medium"
                    />
                  </button>
                </Tooltip>
              </div>
              <div className="quality-control-options__row">
                <label className="quality-control-options__item">
                  <input
                    type="radio"
                    name="qualityControlLevel"
                    checked={formData.qualityControlLevel === 'comprehensive'}
                    onClick={() =>
                      setFormData((prev) => ({
                        ...prev,
                        qualityControlLevel:
                          prev.qualityControlLevel === 'comprehensive'
                            ? ''
                            : 'comprehensive',
                      }))
                    }
                    onChange={() => {}}
                  />
                  <span>{t('knowledgeBase.comprehensiveQualityControl')}</span>
                </label>
                <Tooltip content="Tooltip to be implemented">
                  <button
                    type="button"
                    className="quality-control-options__info-btn"
                    aria-label={t('knowledgeBase.comprehensiveQualityControlInfo') as string}
                  >
                    <Icon
                      className="quality-control-options__info"
                      icon={<MdInfoOutline fontSize={18} color="#005AA3" />}
                      size="medium"
                    />
                  </button>
                </Tooltip>
              </div>
            </div>
          </Track>
        </Dialog>
      )}

      {/* Add URL List Modal */}
      {addUrlListModal && (
        <Dialog
          title={t('knowledgeBase.addUrlList')}
          onClose={() => setAddUrlListModal(false)}
          footer={
            <Track gap={16} justify="end">
              <Button
                appearance="secondary"
                onClick={() => setAddUrlListModal(false)}
              >
                {t('global.cancel')}
              </Button>
              <Button
                appearance="primary"
                onClick={handleAddUrlList}
                disabled={
                  addUrlListMutation.isLoading ||
                  !formData.subsector ||
                  !formData.websiteUrl ||
                  !formData.urlList ||
                  formData.urlList.length === 0
                }
              >
                {addUrlListMutation.isLoading
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
              label={t('knowledgeBase.mainSourceUrl')}
              name="websiteUrl"
              value={formData.websiteUrl || ''}
              onChange={(e) =>
                setFormData((prev) => ({ ...prev, websiteUrl: e.target.value }))
              }
              required
            />
            
            <div style={{ marginTop: '16px', width: '100%', textAlign: 'left', alignSelf: 'flex-start' }}>
              <label style={{ display: 'block', marginBottom: '8px', fontWeight: 500 }}>
                {t('knowledgeBase.uploadCsvWithUrls')}
              </label>
              <input
                type="file"
                accept=".csv"
                onChange={handleCsvFileChange}
                style={{
                  display: 'block',
                  width: '100%',
                  padding: '8px',
                  border: '1px solid #ccc',
                  borderRadius: '4px',
                }}
              />
              <small style={{ display: 'block', marginTop: '4px', color: '#666', fontSize: '12px', lineHeight: 1.3 }}>
                {t('knowledgeBase.csvUrlHelp')}
              </small>
            </div>

            {formData.urlList && formData.urlList.length > 0 && (
              <div style={{ 
                marginTop: '16px', 
                width: '100%',
                textAlign: 'left',
                alignSelf: 'flex-start',
                padding: '12px', 
                backgroundColor: '#f5f5f5', 
                borderRadius: '4px',
                maxHeight: '200px',
                overflowY: 'auto'
              }}>
                <strong>{formData.urlList.length} URLs loaded:</strong>
                <ul style={{ 
                  marginTop: '8px', 
                  paddingLeft: '20px',
                  fontSize: '14px',
                  listStyle: 'disc'
                }}>
                  {formData.urlList.slice(0, 10).map((item, index) => (
                    <li key={index} style={{ marginBottom: '4px' }}>
                      {item.url}
                    </li>
                  ))}
                  {formData.urlList.length > 10 && (
                    <li style={{ fontStyle: 'italic', color: '#666' }}>
                      ... and {formData.urlList.length - 10} more
                    </li>
                  )}
                </ul>
              </div>
            )}

            <div className="quality-control-options">
              <span className="quality-control-options__title">
                {t('knowledgeBase.contentExtractionQualityControlOptions')}
              </span>
              <div
                className="quality-control-options__row"
                style={{ flexDirection: 'column', alignItems: 'flex-start' }}
              >
                <label className="quality-control-options__item">
                  <input
                    type="radio"
                    name="qualityControlLevelUrlList"
                    checked={formData.qualityControlLevel === 'basic'}
                    onClick={() =>
                      setFormData((prev) => ({
                        ...prev,
                        qualityControlLevel:
                          prev.qualityControlLevel === 'basic' ? '' : 'basic',
                      }))
                    }
                    onChange={() => {}}
                  />
                  <span>{t('knowledgeBase.basicQualityControl')}</span>
                </label>
                <label className="quality-control-options__item">
                  <input
                    type="radio"
                    name="qualityControlLevelUrlList"
                    checked={formData.qualityControlLevel === 'comprehensive'}
                    onClick={() =>
                      setFormData((prev) => ({
                        ...prev,
                        qualityControlLevel:
                          prev.qualityControlLevel === 'comprehensive'
                            ? ''
                            : 'comprehensive',
                      }))
                    }
                    onChange={() => {}}
                  />
                  <span>{t('knowledgeBase.comprehensiveQualityControl')}</span>
                </label>
              </div>
            </div>
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