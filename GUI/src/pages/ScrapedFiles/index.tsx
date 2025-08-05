import { FC, useState, useMemo } from 'react';
import { useTranslation } from 'react-i18next';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useParams, Link } from 'react-router-dom';
import {
  MdRefresh,
  MdOutlineViewColumn,
  MdOutlineTableChart,
  MdGridView,
  MdOutlineDeleteOutline,
} from 'react-icons/md';
import {
  Button,
  Card,
  DataTable,
  Dialog,
  FormInput,
  Icon,
  Track,
  SwitchBox,
  Editor,
  Tooltip,
} from 'components';
import {
  ColumnDef,
  PaginationState,
  SortingState,
  ColumnFiltersState,
} from '@tanstack/react-table';
import { useToast } from 'hooks/useToast';
import 'pages/Agency/AgencyList.scss';
import {
  getScrapedFiles,
  updateFileExclusion,
  refreshScrapedFile,
  deleteFile,
  downloadFile,
  fetchFileData,
  updateFileEditedContentWithUpload,
  ScrapedFile,
  ScrapedFilesListParams,
  EditorState,
} from 'services/files';
import { getSource } from 'services/sources';

interface FormData {
  search: string;
}

const ScrapedFiles: FC = () => {
  const { t } = useTranslation();
  const toast = useToast();
  const queryClient = useQueryClient();
  const { id: sourceId } = useParams<{ id: string }>();

  const [deleteModal, setDeleteModal] = useState<ScrapedFile | null>(null);
  const [editorState, setEditorState] = useState<EditorState | null>(null);
  const [formData, setFormData] = useState<FormData>({
    search: '',
  });
  const [searchQuery, setSearchQuery] = useState('');

  // Table state for server-side pagination and sorting
  const [pagination, setPagination] = useState<PaginationState>({
    pageIndex: 0,
    pageSize: 10,
  });

  // Handle search input change
  const handleSearchChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setFormData({ search: e.target.value });
  };

  // Handle search button click
  const handleSearchSubmit = () => {
    setSearchQuery(formData.search);
    setPagination((prev) => ({ ...prev, pageIndex: 0 }));
  };

  // Handle Enter key in search input
  const handleSearchKeyPress = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      handleSearchSubmit();
    }
  };

  // Fetch source data for header
  const { data: sourceData } = useQuery({
    queryKey: ['source', sourceId],
    queryFn: () => getSource(sourceId!),
    enabled: !!sourceId,
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
      url: 'url',
      pageTitle: 'page_title',
      isExcluded: 'is_excluded',
      status: 'status',
      lastScrapedAt: 'last_scraped_at',
    };

    field = fieldMap[field] || field;
    return `${field} ${sort.desc ? 'desc' : 'asc'}`;
  };

  // API query parameters
  const queryParams: ScrapedFilesListParams = useMemo(
    () => ({
      sourceId: sourceId,
      page: pagination.pageIndex + 1,
      pageSize: pagination.pageSize,
      sorting: getSortingParam(sorting),
      search: searchQuery || undefined, // Only include search if it's not empty
    }),
    [sourceId, pagination.pageIndex, pagination.pageSize, sorting, searchQuery]
  );

  // Fetch scraped files data
  const {
    data: scrapedFilesData,
    isLoading,
    refetch,
  } = useQuery({
    queryKey: ['scrapedFiles', queryParams],
    queryFn: () => getScrapedFiles(queryParams),
    enabled: !!sourceId,
    keepPreviousData: true,
  });

  // Refresh file mutation
  const refreshMutation = useMutation({
    mutationFn: refreshScrapedFile,
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlRefreshed'),
      });
      queryClient.invalidateQueries(['scrapedFiles']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.refreshError'),
      });
    },
  });

  // Delete file mutation
  const deleteMutation = useMutation({
    mutationFn: deleteFile,
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.urlDeleteSuccess'),
      });
      setDeleteModal(null);
      queryClient.invalidateQueries(['scrapedFiles']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.deleteError'),
      });
    },
  });

  // Update exclusion mutation
  const updateExclusionMutation = useMutation({
    mutationFn: ({
      fileId,
      isExcluded,
    }: {
      fileId: string;
      isExcluded: boolean;
    }) => updateFileExclusion(fileId, isExcluded),
    onSuccess: () => {
      queryClient.invalidateQueries(['scrapedFiles']);
    },
    onError: (error: any) => {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.updateError'),
      });
    },
  });

  // Save edited content mutation
  const saveContentMutation = useMutation({
    mutationFn: ({ content }: { content: string }) => {
      if (!editorState?.file) {
        throw new Error('No file available for saving');
      }
      setEditorState({ ...editorState, saving: true });
      const sourcePath = `${sourceData?.agencyBaseId}/${sourceData?.baseId}/${editorState.file.baseId}`;
      return updateFileEditedContentWithUpload(
        editorState.file,
        content,
        sourcePath,
      );
    },
    onSuccess: () => {
      toast.open({
        type: 'success',
        title: t('global.notification'),
        message: t('knowledgeBase.contentSaved'),
      });

      queryClient.invalidateQueries(['scrapedFiles']);
      setEditorState(null);
    },
    onError: (error: any) => {
      setEditorState(null);
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.saveError'),
      });
    },
  });

  const handleRefresh = (file: ScrapedFile) => {
    refreshMutation.mutate(file.baseId);
  };

  const handleDelete = () => {
    if (!deleteModal) return;
    deleteMutation.mutate(deleteModal.baseId);
  };

  const handleExclusionToggle = (file: ScrapedFile, isExcluded: boolean) => {
    updateExclusionMutation.mutate({
      fileId: file.baseId,
      isExcluded,
    });
  };

  const handleViewContent = async (
    file: ScrapedFile,
    type: 'raw' | 'cleaned' | 'edited'
  ) => {
    try {
      // If it's raw content, download the file instead of viewing
      if (type === 'raw') {
        await handleDownloadFile(file);
        return;
      }

      setEditorState({
        type,
        file,
        content: '',
        loading: true,
        sourceType: 'scraped',
      });

      // For cleaned and edited content, continue with existing logic
      const path =
        type === 'cleaned' ? file.cleanedDataUrl : file.editedDataUrl;
      if (!path) return;
      const content = await fetchFileData(path);

      setEditorState({
        type,
        file,
        content,
        loading: false,
        sourceType: 'scraped',
      });
    } catch (error) {
      setEditorState(null);
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.contentLoadError'),
      });
    }
  };

  const handleEditorStateChange = async (newState: EditorState) => {
    if (!newState.file) return;

    try {
      // If it's raw content, download the file instead of viewing
      if (newState.type === 'raw') {
        await handleDownloadFile(newState.file as ScrapedFile);
        return;
      }

      setEditorState({
        ...newState,
        loading: true,
        content: '',
      });

      // Fetch content based on type
      let content = '';
      const uploadedFile = newState.file as ScrapedFile;

      const editedPath =
        newState.type === 'edited'
          ? uploadedFile.editedDataUrl
          : uploadedFile.cleanedDataUrl;
      if (editedPath) {
        content = await fetchFileData(editedPath);
      }

      setEditorState({
        ...newState,
        content,
        loading: false,
      });
    } catch (error) {
      setEditorState({
        ...newState,
        loading: false,
      });
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: t('knowledgeBase.contentLoadError'),
      });
    }
  };

  const handleDownloadFile = async (file: ScrapedFile) => {
    try {
      // Get the download URL from backend
      if (!file.originalDataUrl) return;
      await downloadFile(file.originalDataUrl, file.fileName);
    } catch (error: any) {
      toast.open({
        type: 'error',
        title: t('global.notificationError'),
        message: error.message || t('knowledgeBase.downloadError'),
      });
    }
  };

  const handleSaveContent = (content: string) => {
    if (!editorState) return;

    saveContentMutation.mutate({ content });
  };

  const handlePaginationChange = (newPagination: PaginationState) => {
    setPagination(newPagination);
  };

  const handleSortingChange = (newSorting: SortingState) => {
    setSorting(newSorting);
  };

  const columns: ColumnDef<ScrapedFile>[] = [
    {
      accessorKey: 'url',
      header: t('knowledgeBase.url'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <a
          href={row.original.url}
          target="_blank"
          rel="noopener noreferrer"
          style={{ textDecoration: 'underline', color: '#005AA3' }}
        >
          <Tooltip content={row.original.url}>
            <div
              style={{
                maxWidth: 500,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
              }}
              className="agencies__agency-cell"
            >
              {row.original.url}
            </div>
          </Tooltip>
        </a>
      ),
    },
    {
      accessorKey: 'pageTitle',
      header: t('knowledgeBase.pageTitle'),
      enableColumnFilter: false,

      cell: ({ row }) => (
        <Tooltip content={row.original.pageTitle}>
          <div
            style={{
              minWidth: 100,
              maxWidth: 120,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}
          >
            {row.original.pageTitle}
          </div>
        </Tooltip>
      ),
    },
    {
      id: 'actions',
      header: '',
      cell: ({ row }) => (
        <Track
          justify="end"
          align="flex-start"
          gap={32}
          style={{ width: 'max-content' }}
        >
          <Button
            disabled={
              row.original.status === 'cleaning' || refreshMutation.isLoading
            }
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => handleRefresh(row.original)}
          >
            <Icon icon={<MdRefresh fontSize={20} />} size="medium" />
            {t('knowledgeBase.refresh')}
          </Button>
          <Button
            disabled={!row.original.originalDataUrl}
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => handleViewContent(row.original, 'raw')}
          >
            <Icon icon={<MdOutlineViewColumn fontSize={20} />} size="medium" />
            {t('knowledgeBase.raw')}
          </Button>
          <Button
            disabled={
              row.original.status === 'cleaning' || !row.original.cleanedDataUrl
            }
            appearance="text"
            className="agencies__action-btn"
            size="s"
            onClick={() => handleViewContent(row.original, 'cleaned')}
          >
            <Icon icon={<MdOutlineTableChart fontSize={20} />} size="medium" />
            {t('knowledgeBase.cleaned')}
          </Button>
          <Button
            appearance="text"
            disabled={
              row.original.status === 'cleaning' || !row.original.editedDataUrl
            }
            className="agencies__action-btn"
            size="s"
            onClick={() => handleViewContent(row.original, 'edited')}
          >
            <Icon icon={<MdGridView fontSize={20} />} size="medium" />
            {t('knowledgeBase.edited')}
          </Button>
          <Button
            appearance="text"
            disabled={row.original.status === 'cleaning'}
            className="agencies__action-btn"
            size="s"
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
    {
      accessorKey: 'excluded',
      id: 'excluded',
      enableColumnFilter: false,
      header: t('knowledgeBase.excluded'),
      cell: ({ row }) => (
        <SwitchBox
          label=""
          checked={row.original.isExcluded}
          onCheckedChange={(checked) =>
            handleExclusionToggle(row.original, checked)
          }
          disabled={
            updateExclusionMutation.isLoading ||
            row.original.status === 'cleaning'
          }
        />
      ),
    },
    {
      accessorKey: 'status',
      header: t('global.status'),
      cell: ({ row }) => {
        const color =
          row.original.status === 'finished'
            ? '#266B42'
            : row.original.status === 'cleaning'
            ? '#94690D'
            : '#AC3232';
        return (
          <span
            className={`agencies__status-cell`}
            style={{
              color: color,
              borderColor: color,
            }}
          >
            {t(`knowledgeBase.${row.original.status}`)}
          </span>
        );
      },
      enableColumnFilter: false,
    },
    {
      accessorKey: 'lastScrapedAt',
      header: t('knowledgeBase.scraped'),
      enableColumnFilter: false,
      cell: ({ row }) => (
        <span>
          {new Date(row.original.lastScrapedAt).toLocaleString('et-EE', {
            day: '2-digit',
            month: '2-digit',
            year: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
          })}
        </span>
      ),
    },
  ];

  if (isLoading) {
    return <div>Loading...</div>;
  }

  return (
    <>
      {editorState && (
        <Dialog size="fullscreen" onClose={() => setEditorState(null)}>
          <Editor
            changeEditorState={handleEditorStateChange}
            editorState={editorState}
            readonly={false}
            onCancel={() => setEditorState(null)}
            onSave={handleSaveContent}
          />
        </Dialog>
      )}

      <div className="agencies">
        <Track
          style={{ marginBottom: 16, width: '100%' }}
          justify="between"
          align="center"
        >
          <div>
            <Link
              to={`/agency/${sourceData?.agencyBaseId}`}
              style={{ textDecoration: 'none' }}
            >
              <span className="agencies__agency">{sourceData?.url}</span>
            </Link>
          </div>
        </Track>

        <Card
          header={
            <Track gap={16} justify="end" align="center">
              <FormInput
                className="agencies__search"
                label={t('knowledgeBase.searchWithinListedSources')}
                name="search"
                value={formData.search}
                handleSearchChange
                onChange={handleSearchChange}
                onKeyPress={handleSearchKeyPress}
              />
              <Button
                appearance="primary"
                onClick={handleSearchSubmit}
                disabled={isLoading}
              >
                {t('global.search')}
              </Button>
            </Track>
          }
        >
          <DataTable
            data={scrapedFilesData?.data ?? []}
            columns={columns}
            pagination={pagination}
            setPagination={handlePaginationChange}
            sorting={sorting}
            setSorting={handleSortingChange}
            columnFilters={columnFilters}
            setFiltering={setColumnFilters}
            sortable
            filterable
            pagesCount={scrapedFilesData?.totalPages ?? 0}
            isClientSide={false}
          />

          <div className="agencies__footer">
            <span className="agencies__total">
              {scrapedFilesData?.total ?? 0} {t('knowledgeBase.results')}
            </span>
          </div>
        </Card>

        {/* Delete Confirmation Modal */}
        {deleteModal && (
          <Dialog
            title={t('knowledgeBase.deleteUrlTitle')}
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
            <p>
              {t('knowledgeBase.deleteUrlConfirmation', {
                url: deleteModal.url,
              })}
            </p>
          </Dialog>
        )}
      </div>
    </>
  );
};

export default ScrapedFiles;
